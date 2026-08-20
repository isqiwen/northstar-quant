"""P1-WP05 的纯内存、PIT-aware 数据质量规则引擎。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math

import polars as pl

from northstar_quant.data_platform.contracts.data_domain import ArtifactSnapshot, QualityStatus
from northstar_quant.data_platform.quality.models import (
    CalendarCoverageResolver,
    CompletenessRule,
    DataQualityError,
    GapRule,
    OrderingRule,
    QualityEvidence,
    QualityEvaluation,
    QualityFinding,
    QualityReferenceDecision,
    QualityRequest,
    QualityRule,
    RangeRule,
    RevisionBaseline,
    RevisionRule,
    SchemaField,
    StalenessRule,
    UniquenessRule,
    canonical_frame_payload,
)


class DataQualityEngine:
    """执行 P1 的十项规则，不读取时钟、网络、数据库或可变存储。

    所有外部事实（交易日历、合约主数据、日历覆盖）通过受限 Protocol 注入，并由引擎重新
    复核 ``available_at``。适配器异常或未来事实不会被当作通过，而是产生 UNKNOWN。
    """

    def evaluate(self, request: QualityRequest) -> QualityEvaluation:
        """返回恰好包含十个规则的候选制品预发布评估。"""

        if not isinstance(request, QualityRequest):
            raise DataQualityError("request 必须是 QualityRequest")
        if canonical_frame_payload(request.frame) != request.evaluated_payload:
            raise DataQualityError(
                "quality.frame 在请求创建后发生变化，拒绝复用旧 evaluated_payload"
            )
        # 每次评估都使用独立 frame；质量 Protocol 得到的也是各自独立副本，不能让一个外部
        # resolver 改写后续规则看到的数据。
        request = replace(request, frame=request.frame.clone())
        findings = (
            self._completeness(request.frame, request.completeness),
            self._uniqueness(request.frame, request.uniqueness),
            self._ordering(request.frame, request.ordering),
            self._schema(request),
            self._ranges(request.frame, request.ranges),
            self._calendar_consistency(request),
            self._contract_consistency(request),
            self._staleness(request),
            self._gap(request),
            self._revision(request),
        )
        return QualityEvaluation(
            artifact=request.artifact,
            checked_at=request.checked_at,
            decision_at=request.decision_at,
            findings=findings,
            critical_rules=request.critical_rules,
            policy_hash=request.policy_hash,
            frame_hash=request.frame_hash,
            evaluated_payload_hash=request.evaluated_payload_hash,
        )

    @staticmethod
    def _completeness(frame: pl.DataFrame, rule: CompletenessRule) -> QualityFinding:
        missing = [column for column in rule.required_columns if column not in frame.columns]
        if missing:
            return _finding(
                QualityRule.COMPLETENESS,
                QualityStatus.FAIL,
                "REQUIRED_COLUMNS_MISSING",
                "必填字段缺失，无法满足完整性要求。",
                {"missing_column_count": len(missing), "row_count": frame.height},
            )
        if frame.height < rule.min_rows:
            return _finding(
                QualityRule.COMPLETENESS,
                QualityStatus.FAIL,
                "MINIMUM_ROW_COUNT_NOT_MET",
                "记录数低于显式最小要求。",
                {"minimum_rows": rule.min_rows, "row_count": frame.height},
            )
        if frame.height == 0:
            return _finding(
                QualityRule.COMPLETENESS,
                QualityStatus.PASS,
                "COMPLETENESS_CONFIRMED",
                "空数据集符合显式的零行和空值比例策略。",
                {"row_count": 0},
            )
        invalid_columns = 0
        maximum_fraction = 0.0
        for column in rule.required_columns:
            fraction = frame.get_column(column).null_count() / frame.height
            maximum_fraction = max(maximum_fraction, fraction)
            if fraction > rule.max_null_fraction:
                invalid_columns += 1
        if invalid_columns:
            return _finding(
                QualityRule.COMPLETENESS,
                QualityStatus.FAIL,
                "NULL_FRACTION_EXCEEDED",
                "必填字段的空值比例超出显式限制。",
                {
                    "invalid_column_count": invalid_columns,
                    "max_null_fraction": rule.max_null_fraction,
                    "observed_max_null_fraction": maximum_fraction,
                },
            )
        return _finding(
            QualityRule.COMPLETENESS,
            QualityStatus.PASS,
            "COMPLETENESS_CONFIRMED",
            "记录数与必填字段空值比例均满足规则。",
            {"row_count": frame.height, "required_column_count": len(rule.required_columns)},
        )

    @staticmethod
    def _uniqueness(frame: pl.DataFrame, rule: UniquenessRule) -> QualityFinding:
        missing = [column for column in rule.primary_key if column not in frame.columns]
        if missing:
            return _finding(
                QualityRule.UNIQUENESS,
                QualityStatus.UNKNOWN,
                "PRIMARY_KEY_COLUMNS_UNAVAILABLE",
                "主键字段不可用，无法判断唯一性。",
                {"missing_column_count": len(missing)},
            )
        duplicate_count = (
            frame.group_by(list(rule.primary_key)).len().filter(pl.col("len") > 1).height
            if frame.height
            else 0
        )
        if duplicate_count:
            return _finding(
                QualityRule.UNIQUENESS,
                QualityStatus.FAIL,
                "DUPLICATE_PRIMARY_KEYS",
                "显式主键存在重复记录。",
                {"duplicate_key_count": duplicate_count},
            )
        return _finding(
            QualityRule.UNIQUENESS,
            QualityStatus.PASS,
            "PRIMARY_KEYS_UNIQUE",
            "显式主键未发现重复记录。",
            {"row_count": frame.height, "primary_key_column_count": len(rule.primary_key)},
        )

    @staticmethod
    def _ordering(frame: pl.DataFrame, rule: OrderingRule) -> QualityFinding:
        required = (*rule.group_by, *rule.order_by)
        missing = [column for column in required if column not in frame.columns]
        if missing:
            return _finding(
                QualityRule.ORDERING,
                QualityStatus.UNKNOWN,
                "ORDERING_COLUMNS_UNAVAILABLE",
                "排序字段不可用，不能通过预排序掩盖原始输入问题。",
                {"missing_column_count": len(missing)},
            )
        previous_by_group: dict[tuple[object, ...], tuple[object, ...]] = {}
        try:
            for row in frame.select(list(required)).iter_rows(named=True):
                group_key = tuple(row[column] for column in rule.group_by)
                order_key = tuple(row[column] for column in rule.order_by)
                if any(value is None for value in (*group_key, *order_key)):
                    return _finding(
                        QualityRule.ORDERING,
                        QualityStatus.UNKNOWN,
                        "ORDERING_VALUES_UNKNOWN",
                        "排序字段包含空值，无法证明原始顺序。",
                        {"row_count": frame.height},
                    )
                previous = previous_by_group.get(group_key)
                # 故意不调用 sort：此处验证的是输入字节表达的自然记录顺序。
                if previous is not None and order_key < previous:
                    return _finding(
                        QualityRule.ORDERING,
                        QualityStatus.FAIL,
                        "INPUT_ORDERING_VIOLATION",
                        "原始输入顺序不满足显式排序规则。",
                        {"row_count": frame.height},
                    )
                previous_by_group[group_key] = order_key
        except TypeError:
            return _finding(
                QualityRule.ORDERING,
                QualityStatus.UNKNOWN,
                "ORDERING_VALUES_NOT_COMPARABLE",
                "排序字段值不可比较，无法判断输入顺序。",
                {"row_count": frame.height},
            )
        return _finding(
            QualityRule.ORDERING,
            QualityStatus.PASS,
            "INPUT_ORDERING_CONFIRMED",
            "原始输入顺序满足显式排序规则。",
            {"group_count": len(previous_by_group), "row_count": frame.height},
        )

    @staticmethod
    def _schema(request: QualityRequest) -> QualityFinding:
        frame = request.frame
        fields: tuple[SchemaField, ...] = request.schema
        missing = [field for field in fields if field.name not in frame.columns]
        mismatched = [
            field
            for field in fields
            if field.name in frame.columns and str(frame.schema[field.name]) != field.dtype
        ]
        non_nullable_nulls = [
            field
            for field in fields
            if field.name in frame.columns
            and not field.nullable
            and frame.get_column(field.name).null_count() > 0
        ]
        expected_names = {field.name for field in fields}
        additional = set(frame.columns).difference(expected_names)
        schema_version_matches = (
            request.artifact.metadata.schema_version == request.expected_artifact_schema_version
        )
        if (
            missing
            or mismatched
            or non_nullable_nulls
            or not schema_version_matches
            or (additional and not request.allow_additional_columns)
        ):
            return _finding(
                QualityRule.SCHEMA,
                QualityStatus.FAIL,
                "SCHEMA_CONTRACT_VIOLATION",
                "字段存在缺失、类型不匹配或不可空字段为空。",
                {
                    "missing_column_count": len(missing),
                    "non_nullable_null_column_count": len(non_nullable_nulls),
                    "additional_column_count": len(additional),
                    "artifact_schema_version_matches": schema_version_matches,
                    "type_mismatch_count": len(mismatched),
                },
            )
        return _finding(
            QualityRule.SCHEMA,
            QualityStatus.PASS,
            "SCHEMA_CONTRACT_CONFIRMED",
            "字段、数据类型和空值约束均匹配显式 schema。",
            {
                "allow_additional_columns": request.allow_additional_columns,
                "schema_field_count": len(fields),
            },
        )

    @staticmethod
    def _ranges(frame: pl.DataFrame, rules: tuple[RangeRule, ...]) -> QualityFinding:
        if not rules:
            return _finding(
                QualityRule.RANGE,
                QualityStatus.PASS,
                "NO_RANGE_RULES_CONFIGURED",
                "未声明数值范围规则，按显式策略记录为通过。",
                {},
            )
        missing = [rule for rule in rules if rule.column not in frame.columns]
        if missing:
            return _finding(
                QualityRule.RANGE,
                QualityStatus.UNKNOWN,
                "RANGE_COLUMNS_UNAVAILABLE",
                "范围字段不可用，无法判断数值范围。",
                {"missing_column_count": len(missing)},
            )
        non_numeric_count = 0
        violation_count = 0
        for rule in rules:
            for value in frame.get_column(rule.column).to_list():
                if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
                    non_numeric_count += 1
                    continue
                numeric = float(value)
                if not math.isfinite(numeric):
                    non_numeric_count += 1
                    continue
                if (rule.minimum is not None and numeric < rule.minimum) or (
                    rule.maximum is not None and numeric > rule.maximum
                ):
                    violation_count += 1
        if non_numeric_count:
            return _finding(
                QualityRule.RANGE,
                QualityStatus.FAIL,
                "RANGE_VALUES_NON_NUMERIC",
                "范围字段含空值、非数值、NaN 或无穷值。",
                {"non_numeric_count": non_numeric_count},
            )
        if violation_count:
            return _finding(
                QualityRule.RANGE,
                QualityStatus.FAIL,
                "RANGE_VIOLATION",
                "数值超出显式闭区间。",
                {"violation_count": violation_count},
            )
        return _finding(
            QualityRule.RANGE,
            QualityStatus.PASS,
            "RANGES_CONFIRMED",
            "所有受约束数值均在显式闭区间内。",
            {"range_rule_count": len(rules)},
        )

    def _calendar_consistency(self, request: QualityRequest) -> QualityFinding:
        resolver = request.calendar_resolver
        if resolver is None:
            return _unknown_without_reference(
                QualityRule.CALENDAR_CONSISTENCY,
                "CALENDAR_REFERENCE_UNAVAILABLE",
                "未注入可审计日历事实，无法判断日历一致性。",
            )
        return self._reference_finding(
            rule=QualityRule.CALENDAR_CONSISTENCY,
            request=request,
            invoke=lambda: resolver.assess_calendar_consistency(
                frame=request.frame.clone(),
                artifact=request.artifact,
                decision_at=request.decision_at,
            ),
        )

    def _contract_consistency(self, request: QualityRequest) -> QualityFinding:
        resolver = request.contract_resolver
        if resolver is None:
            return _unknown_without_reference(
                QualityRule.CONTRACT_CONSISTENCY,
                "CONTRACT_REFERENCE_UNAVAILABLE",
                "未注入可审计合约事实，无法判断合约一致性。",
            )
        return self._reference_finding(
            rule=QualityRule.CONTRACT_CONSISTENCY,
            request=request,
            invoke=lambda: resolver.assess_contract_consistency(
                frame=request.frame.clone(),
                artifact=request.artifact,
                decision_at=request.decision_at,
            ),
        )

    @staticmethod
    def _staleness(request: QualityRequest) -> QualityFinding:
        rule: StalenessRule = request.staleness
        age = request.decision_at - request.artifact.metadata.acquired_at
        if age >= rule.fail_after:
            return _finding(
                QualityRule.STALENESS,
                QualityStatus.FAIL,
                "STALE_ARTIFACT",
                "制品取得时间超过显式失效阈值。",
                {
                    "age_seconds": age.total_seconds(),
                    "fail_after_seconds": rule.fail_after.total_seconds(),
                },
            )
        if rule.warn_after is not None and age >= rule.warn_after:
            return _finding(
                QualityRule.STALENESS,
                QualityStatus.WARN,
                "ARTIFACT_FRESHNESS_WARNING",
                "制品取得时间超过显式预警阈值。",
                {
                    "age_seconds": age.total_seconds(),
                    "warn_after_seconds": rule.warn_after.total_seconds(),
                },
            )
        return _finding(
            QualityRule.STALENESS,
            QualityStatus.PASS,
            "ARTIFACT_FRESHNESS_CONFIRMED",
            "制品取得时间满足显式时效阈值。",
            {
                "age_seconds": age.total_seconds(),
                "fail_after_seconds": rule.fail_after.total_seconds(),
            },
        )

    def _gap(self, request: QualityRequest) -> QualityFinding:
        rule: GapRule = request.gap
        resolver = request.calendar_coverage_resolver
        required = (*rule.group_by, rule.timestamp_column)
        missing = [column for column in required if column not in request.frame.columns]
        if missing:
            return _finding(
                QualityRule.GAP,
                QualityStatus.UNKNOWN,
                "GAP_COLUMNS_UNAVAILABLE",
                "间隔字段不可用，无法判断数据缺口。",
                {"missing_column_count": len(missing)},
            )
        if resolver is None:
            return _unknown_without_reference(
                QualityRule.GAP,
                "CALENDAR_COVERAGE_UNAVAILABLE",
                "未注入可审计日历覆盖，不能把非交易时段误判为数据缺口。",
            )
        if rule.coverage_start is None or rule.coverage_end is None:
            return _finding(
                QualityRule.GAP,
                QualityStatus.UNKNOWN,
                "GAP_COVERAGE_WINDOW_UNAVAILABLE",
                "未声明明确覆盖起止，不能猜测首尾数据缺口。",
                {},
            )
        timestamps_by_group: dict[tuple[object, ...], list[datetime]] = {}
        try:
            for row in request.frame.select(list(required)).iter_rows(named=True):
                group_key = tuple(row[column] for column in rule.group_by)
                timestamp = _as_utc_timestamp(row[rule.timestamp_column])
                if timestamp is None:
                    return _finding(
                        QualityRule.GAP,
                        QualityStatus.UNKNOWN,
                        "GAP_TIMESTAMP_UNKNOWN",
                        "间隔字段含未知或无时区时间，无法判断数据缺口。",
                        {"row_count": request.frame.height},
                    )
                if timestamp < rule.coverage_start or timestamp > rule.coverage_end:
                    return _finding(
                        QualityRule.GAP,
                        QualityStatus.FAIL,
                        "GAP_OBSERVATION_OUTSIDE_COVERAGE_WINDOW",
                        "观测时间超出显式 gap 覆盖窗口。",
                        {"row_count": request.frame.height},
                    )
                timestamps = timestamps_by_group.setdefault(group_key, [])
                if timestamps and timestamp < timestamps[-1]:
                    return _finding(
                        QualityRule.GAP,
                        QualityStatus.UNKNOWN,
                        "GAP_INPUT_NOT_ORDERED",
                        "原始输入未按时间递增，缺口判断拒绝自行排序。",
                        {"row_count": request.frame.height},
                    )
                timestamps.append(timestamp)
        except TypeError:
            return _finding(
                QualityRule.GAP,
                QualityStatus.UNKNOWN,
                "GAP_GROUP_VALUES_UNUSABLE",
                "分组字段不可用，无法按原始记录顺序判断缺口。",
                {"row_count": request.frame.height},
            )
        if not timestamps_by_group:
            return _finding(
                QualityRule.GAP,
                QualityStatus.UNKNOWN,
                "GAP_INSUFFICIENT_OBSERVATIONS",
                "观测不足，无法在日历覆盖下证明不存在缺口。",
                {"row_count": request.frame.height},
            )
        insufficient_groups = [
            timestamps for timestamps in timestamps_by_group.values() if len(timestamps) < 2
        ]
        if insufficient_groups:
            return _finding(
                QualityRule.GAP,
                QualityStatus.UNKNOWN,
                "GAP_GROUP_OBSERVATIONS_INSUFFICIENT",
                "至少一个分组没有足够观测，无法确认内部和边界缺口。",
                {
                    "group_count": len(timestamps_by_group),
                    "insufficient_group_count": len(insufficient_groups),
                },
            )
        covered_interval_count = 0
        excess_gap_count = 0
        for timestamps in timestamps_by_group.values():
            intervals = ((rule.coverage_start, timestamps[0]), *zip(timestamps, timestamps[1:]), (timestamps[-1], rule.coverage_end))
            for start, end in intervals:
                if start == end:
                    continue
                covered_interval_count += 1
                if end - start > rule.maximum_gap:
                    excess_gap_count += 1
                finding = self._gap_interval(
                    resolver=resolver,
                    request=request,
                    start=start,
                    end=end,
                    maximum_gap=rule.maximum_gap,
                )
                if finding is not None:
                    return finding
        if not covered_interval_count:
            return _finding(
                QualityRule.GAP,
                QualityStatus.UNKNOWN,
                "GAP_COVERAGE_INTERVALS_UNAVAILABLE",
                "覆盖窗口内没有可由日历裁决的非零观测间隔。",
                {"group_count": len(timestamps_by_group)},
            )
        return _finding(
            QualityRule.GAP,
            QualityStatus.PASS,
            "CALENDAR_AWARE_COVERAGE_INTERVALS_CONFIRMED",
            "在显式覆盖窗口和可见日历事实下未发现应有观测区间的超限缺口。",
            {
                "covered_interval_count": covered_interval_count,
                "excess_gap_count": excess_gap_count,
                "group_count": len(timestamps_by_group),
            },
        )

    def _gap_interval(
        self,
        *,
        resolver: CalendarCoverageResolver,
        request: QualityRequest,
        start: datetime,
        end: datetime,
        maximum_gap: timedelta,
    ) -> QualityFinding | None:
        reference = self._coverage_decision(
            resolver=resolver,
            request=request,
            start=start,
            end=end,
        )
        if isinstance(reference, QualityFinding):
            return reference
        if reference.status is QualityStatus.FAIL:
            return _finding(
                QualityRule.GAP,
                QualityStatus.FAIL,
                "CALENDAR_COVERAGE_REJECTED",
                "日历覆盖事实明确拒绝该间隔，无法将其视为合法停市。",
                _reference_evidence(reference),
            )
        if reference.status is QualityStatus.UNKNOWN:
            return _finding(
                QualityRule.GAP,
                QualityStatus.UNKNOWN,
                "CALENDAR_COVERAGE_UNKNOWN",
                "日历覆盖事实未能确认该间隔，不能把缺口结论视为通过。",
                _reference_evidence(reference),
            )
        if reference.expected_observation is None:
            return _finding(
                QualityRule.GAP,
                QualityStatus.UNKNOWN,
                "CALENDAR_COVERAGE_EXPECTATION_UNKNOWN",
                "日历覆盖未说明该间隔是否应有观测。",
                _reference_evidence(reference),
            )
        if end - start > maximum_gap and reference.expected_observation:
            return _finding(
                QualityRule.GAP,
                QualityStatus.FAIL,
                "DATA_GAP_DURING_COVERED_INTERVAL",
                "日历覆盖的应有观测区间存在超限缺口。",
                _reference_evidence(reference),
            )
        return None

    def _revision(self, request: QualityRequest) -> QualityFinding:
        rule: RevisionRule = request.revision
        baseline = rule.baseline
        if baseline is None:
            return _finding(
                QualityRule.REVISION,
                QualityStatus.UNKNOWN,
                "REVISION_BASELINE_UNAVAILABLE",
                "没有显式 prior immutable baseline，不能把 revision 静默视为通过。",
                {},
            )
        candidate_snapshot = ArtifactSnapshot.from_artifact(request.artifact)
        baseline_snapshot = baseline.artifact_snapshot
        if baseline_snapshot.quality_status in {QualityStatus.FAIL, QualityStatus.UNKNOWN}:
            return _finding(
                QualityRule.REVISION,
                QualityStatus.UNKNOWN,
                "REVISION_BASELINE_QUALITY_UNTRUSTED",
                "prior immutable baseline 的质量状态不可用于 revision 判断。",
                {"baseline_snapshot_hash": baseline_snapshot.snapshot_hash},
            )
        identity_mismatches = (
            baseline_snapshot.kind != candidate_snapshot.kind,
            baseline_snapshot.source_id != candidate_snapshot.source_id,
            baseline_snapshot.schema_version != candidate_snapshot.schema_version,
            baseline_snapshot.transform_version != candidate_snapshot.transform_version,
        )
        if any(identity_mismatches):
            return _finding(
                QualityRule.REVISION,
                QualityStatus.UNKNOWN,
                "REVISION_BASELINE_IDENTITY_MISMATCH",
                "prior immutable baseline 与候选制品的来源或规范身份不一致。",
                {"baseline_snapshot_hash": baseline_snapshot.snapshot_hash},
            )
        if baseline.artifact_snapshot.available_at > request.checked_at or (
            baseline.artifact_snapshot.available_at > request.decision_at
        ):
            return _finding(
                QualityRule.REVISION,
                QualityStatus.UNKNOWN,
                "REVISION_BASELINE_NOT_VISIBLE_AT_PIT",
                "prior immutable baseline 在当前 PIT 尚不可见。",
                {"baseline_snapshot_hash": baseline.artifact_snapshot.snapshot_hash},
            )
        missing = [
            column
            for column in (*rule.key_columns, *rule.content_columns)
            if column not in request.frame.columns
        ]
        if missing:
            return _finding(
                QualityRule.REVISION,
                QualityStatus.UNKNOWN,
                "REVISION_COLUMNS_UNAVAILABLE",
                "revision 主键或内容字段不可用。",
                {"missing_column_count": len(missing)},
            )
        try:
            current = RevisionBaseline.from_frame(
                artifact=request.artifact,
                frame=request.frame,
                key_columns=rule.key_columns,
                content_columns=rule.content_columns,
            )
        except DataQualityError:
            return _finding(
                QualityRule.REVISION,
                QualityStatus.UNKNOWN,
                "REVISION_CURRENT_BASELINE_INVALID",
                "当前记录无法构造可审计 revision 比较基线。",
                {},
            )
        prior_by_key = {record.key_json: record.content_hash for record in baseline.records}
        current_by_key = {record.key_json: record.content_hash for record in current.records}
        overlap = set(prior_by_key).intersection(current_by_key)
        added = set(current_by_key).difference(prior_by_key)
        removed = set(prior_by_key).difference(current_by_key)
        if not overlap:
            return _finding(
                QualityRule.REVISION,
                QualityStatus.UNKNOWN,
                "REVISION_BASELINE_NO_KEY_OVERLAP",
                "prior immutable baseline 与当前数据没有可比较的同主键记录。",
                {
                    "added_key_count": len(added),
                    "baseline_snapshot_hash": baseline.artifact_snapshot.snapshot_hash,
                    "removed_key_count": len(removed),
                },
            )
        changed = sum(
            1
            for key, content_hash in current_by_key.items()
            if key in prior_by_key and prior_by_key[key] != content_hash
        )
        if changed or removed:
            return _finding(
                QualityRule.REVISION,
                rule.on_change_status,
                "SAME_KEY_CONTENT_REVISED_OR_PRIOR_KEYS_REMOVED",
                "同主键内容已修订或 prior immutable baseline 的记录已消失。",
                {
                    "added_key_count": len(added),
                    "baseline_snapshot_hash": baseline.artifact_snapshot.snapshot_hash,
                    "changed_key_count": changed,
                    "removed_key_count": len(removed),
                },
            )
        return _finding(
            QualityRule.REVISION,
            QualityStatus.PASS,
            "REVISION_COMPARISON_CONFIRMED",
            "与可见 prior immutable baseline 的同主键内容一致。",
            {
                "baseline_snapshot_hash": baseline.artifact_snapshot.snapshot_hash,
                "added_key_count": len(added),
                "compared_key_count": len(overlap),
                "removed_key_count": len(removed),
            },
        )

    def _reference_finding(
        self,
        *,
        rule: QualityRule,
        request: QualityRequest,
        invoke: Callable[[], QualityReferenceDecision],
    ) -> QualityFinding:
        reference = self._visible_reference_decision(rule=rule, request=request, invoke=invoke)
        if isinstance(reference, QualityFinding):
            return reference
        return _finding(
            rule,
            reference.status,
            reference.reason_code,
            reference.summary,
            _reference_evidence(reference),
        )

    def _coverage_decision(
        self,
        *,
        resolver: CalendarCoverageResolver,
        request: QualityRequest,
        start: datetime,
        end: datetime,
    ) -> QualityReferenceDecision | QualityFinding:
        return self._visible_reference_decision(
            rule=QualityRule.GAP,
            request=request,
            invoke=lambda: resolver.assess_expected_observation(
                start=start,
                end=end,
                artifact=request.artifact,
                decision_at=request.decision_at,
            ),
        )

    @staticmethod
    def _visible_reference_decision(
        *,
        rule: QualityRule,
        request: QualityRequest,
        invoke: Callable[[], QualityReferenceDecision],
    ) -> QualityReferenceDecision | QualityFinding:
        try:
            reference = invoke()
        except Exception:  # 外部事实适配器异常必须失败关闭而不是让调用方继续。
            return _finding(
                rule,
                QualityStatus.UNKNOWN,
                "REFERENCE_ADAPTER_ERROR",
                "外部事实适配器未能给出可审计结论。",
                {"adapter_error": True},
            )
        if not isinstance(reference, QualityReferenceDecision):
            return _finding(
                rule,
                QualityStatus.UNKNOWN,
                "REFERENCE_DECISION_INVALID",
                "外部事实适配器没有返回受支持的审计决策。",
                {},
            )
        if reference.available_at is None:
            return _finding(
                rule,
                QualityStatus.UNKNOWN,
                "REFERENCE_AVAILABLE_AT_UNKNOWN",
                "外部事实缺少可见时间，不能用于当前 PIT。",
                {},
            )
        if reference.available_at > request.checked_at or reference.available_at > request.decision_at:
            return _finding(
                rule,
                QualityStatus.UNKNOWN,
                "REFERENCE_NOT_VISIBLE_AT_PIT",
                "外部事实在当前 checked_at 或 decision_at 尚不可见。",
                {"reference_hash": reference.reference_hash or "unknown"},
            )
        return reference


def _finding(
    rule: QualityRule,
    status: QualityStatus,
    reason_code: str,
    summary: str,
    evidence: dict[str, object],
) -> QualityFinding:
    return QualityFinding(
        rule=rule,
        status=status,
        reason_code=reason_code,
        summary=summary,
        evidence=QualityEvidence.from_mapping(evidence),
    )


def _unknown_without_reference(rule: QualityRule, reason_code: str, summary: str) -> QualityFinding:
    return _finding(rule, QualityStatus.UNKNOWN, reason_code, summary, {})


def _reference_evidence(reference: QualityReferenceDecision) -> dict[str, object]:
    evidence: dict[str, object] = {
        "reference_available_at": reference.available_at.astimezone(UTC).isoformat()
        if reference.available_at is not None
        else "unknown",
        "reference_evidence": reference.evidence.as_mapping(),
        "reference_hash": reference.reference_hash or "unknown",
    }
    if reference.expected_observation is not None:
        evidence["expected_observation"] = reference.expected_observation
    return evidence


def _as_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


__all__ = ["DataQualityEngine"]
