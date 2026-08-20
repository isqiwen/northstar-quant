"""P1-WP05 数据质量引擎的不可变审计模型。

本模块只描述候选制品的预发布质量评估；不会写入不可变制品库、文件系统、网络或数据库。
制品发布和 assessment 持久化将在后续数据源适配工作包接线。这里故意不修改
``DataQualityResult``：后者表示已经以相同质量状态构造并准备发布的制品记录。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
import json
import math
import re
from typing import Protocol

import polars as pl

from northstar_quant.data_platform.artifacts.fingerprints import (
    canonical_json_sha256,
    content_sha256,
)
from northstar_quant.data_platform.contracts.data_domain import (
    Artifact,
    ArtifactSnapshot,
    DataDomainError,
    DataQualityResult,
    QualityStatus,
)


class DataQualityError(ValueError):
    """质量规则、审计证据或预发布绑定不满足安全约束。"""


class QualityRule(str, Enum):
    """P1-WP05 的固定质量规则集合。"""

    COMPLETENESS = "completeness"
    UNIQUENESS = "uniqueness"
    ORDERING = "ordering"
    SCHEMA = "schema"
    RANGE = "range"
    CALENDAR_CONSISTENCY = "calendar_consistency"
    CONTRACT_CONSISTENCY = "contract_consistency"
    STALENESS = "staleness"
    GAP = "gap"
    REVISION = "revision"


class QualityMode(str, Enum):
    """质量结论的下游用途。"""

    RESEARCH = "research"
    PRODUCTION = "production"


_QUALITY_PRECEDENCE = {
    QualityStatus.PASS: 0,
    QualityStatus.WARN: 1,
    QualityStatus.UNKNOWN: 2,
    QualityStatus.FAIL: 3,
}
_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization|bearer|api[ _-]?key|credential|token|secret|password|passwd|cookie)"
)
_CANONICAL_FRAME_FORMAT = "northstar.data_quality.canonical_frame.v1"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataQualityError(f"{field_name} 必须是非空文本")
    text = value.strip()
    if _SECRET_PATTERN.search(text):
        raise DataQualityError(f"{field_name} 不得包含凭据、令牌或授权头")
    if text.startswith(("/", "\\\\", "~/", "~\\")) or re.match(r"^[A-Za-z]:[\\\\/]", text):
        raise DataQualityError(f"{field_name} 不得包含本机绝对路径")
    return text


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataQualityError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(UTC)


def _non_negative_timedelta(value: object, field_name: str) -> timedelta:
    if not isinstance(value, timedelta) or value < timedelta(0):
        raise DataQualityError(f"{field_name} 必须是非负 timedelta")
    return value


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataQualityError(f"{field_name} 必须是有限数值")
    result = float(value)
    if not math.isfinite(result):
        raise DataQualityError(f"{field_name} 必须是有限数值")
    return result


def _canonical_json_value(value: object, field_name: str, depth: int = 0) -> object:
    """将审计证据限制为有限 JSON 标量与映射，并递归去除可变容器。"""

    if depth > 4:
        raise DataQualityError(f"{field_name} 的嵌套层级不得超过 4")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataQualityError(f"{field_name} 不得包含 NaN 或无穷值")
        return value
    if isinstance(value, str):
        return _required_text(value, field_name)
    if not isinstance(value, Mapping):
        raise DataQualityError(f"{field_name} 只能包含 JSON 有限标量或映射")
    if len(value) > 64:
        raise DataQualityError(f"{field_name} 的映射条目不得超过 64")
    normalized: dict[str, object] = {}
    for key, nested in value.items():
        clean_key = _required_text(key, f"{field_name}.key")
        if clean_key in normalized:
            raise DataQualityError(f"{field_name} 不能包含重复键")
        normalized[clean_key] = _canonical_json_value(nested, f"{field_name}.{clean_key}", depth + 1)
    return {key: normalized[key] for key in sorted(normalized)}


def canonical_frame_payload(frame: pl.DataFrame) -> bytes:
    """生成唯一允许进入本质量核心的 canonical tabular payload bytes。

    P1-WP05 不尝试猜测 raw、Parquet 或供应商私有格式如何映射到 DataFrame。调用方必须先将
    表格规范化为这个带 schema、列顺序和原始行顺序的 JSON 格式，并以同一 bytes 构造候选
    artifact。无法满足该绑定的格式会在 WP06 的 adapter/publisher 链路中处理；本核心拒绝
    对它们声明可发布质量结论。
    """

    if not isinstance(frame, pl.DataFrame):
        raise DataQualityError("quality.frame 必须是 Polars DataFrame")
    payload = {
        "format": _CANONICAL_FRAME_FORMAT,
        "schema": [
            {"dtype": str(frame.schema[column]), "name": column}
            for column in frame.columns
        ],
        "rows": [
            {
                column: _canonical_frame_cell(row[column], f"quality.frame.{column}")
                for column in frame.columns
            }
            for row in frame.iter_rows(named=True)
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_frame_cell(value: object, field_name: str) -> dict[str, object]:
    """保留类型和特殊数值，避免数据值的文本表达碰撞。"""

    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "int", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            encoded = "nan"
        elif math.isinf(value):
            encoded = "inf" if value > 0 else "-inf"
        else:
            encoded = value.hex()
        return {"type": "float", "value": encoded}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"type": "time", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    raise DataQualityError(f"{field_name} 包含不支持的嵌套或运行时值类型")


@dataclass(frozen=True, slots=True)
class QualityEvidence:
    """可回放且无敏感信息的结构化证据。

    ``canonical_json`` 是唯一存储形态，避免可变字典在报告创建后被篡改。调用 ``as_mapping``
    时会返回新的对象副本，供展示或序列化使用。
    """

    canonical_json: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_json, str) or not self.canonical_json:
            raise DataQualityError("evidence.canonical_json 必须是非空 JSON")
        try:
            value = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise DataQualityError("evidence.canonical_json 必须是有效 JSON") from exc
        normalized = _canonical_json_value(value, "evidence")
        if not isinstance(normalized, dict):
            raise DataQualityError("evidence 必须是 JSON 映射")
        canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if canonical != self.canonical_json:
            raise DataQualityError("evidence.canonical_json 必须是 canonical JSON")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None = None) -> "QualityEvidence":
        normalized = _canonical_json_value(values or {}, "evidence")
        if not isinstance(normalized, dict):  # 仅为类型收窄；上方调用固定传入映射。
            raise DataQualityError("evidence 必须是 JSON 映射")
        return cls(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )

    def as_mapping(self) -> dict[str, object]:
        """返回可安全读取的独立 JSON 映射副本。"""

        return json.loads(self.canonical_json)


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """一条规则的不可变、可审计结论。"""

    rule: QualityRule
    status: QualityStatus
    reason_code: str
    summary: str
    evidence: QualityEvidence = field(default_factory=QualityEvidence.from_mapping)

    def __post_init__(self) -> None:
        if not isinstance(self.rule, QualityRule):
            raise DataQualityError("finding.rule 必须是 QualityRule")
        if not isinstance(self.status, QualityStatus):
            raise DataQualityError("finding.status 必须是 QualityStatus")
        reason_code = _required_text(self.reason_code, "finding.reason_code")
        if _REASON_CODE_RE.fullmatch(reason_code) is None:
            raise DataQualityError("finding.reason_code 必须是大写稳定原因码")
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "summary", _required_text(self.summary, "finding.summary"))
        if not isinstance(self.evidence, QualityEvidence):
            raise DataQualityError("finding.evidence 必须是 QualityEvidence")

    @property
    def fingerprint(self) -> str:
        return canonical_json_sha256(
            {
                "evidence": self.evidence.as_mapping(),
                "reason_code": self.reason_code,
                "rule": self.rule.value,
                "status": self.status.value,
                "summary": self.summary,
            }
        )


@dataclass(frozen=True, slots=True)
class QualityReferenceDecision:
    """日历、合约或覆盖范围适配器返回的纯事实结论。

    该对象只允许 ``PASS``、``FAIL``、``UNKNOWN``，并带明确 ``available_at``。引擎会再次
    验证其可用时间不晚于 ``checked_at``，防止未来日历或规则泄漏进当前评估。
    """

    status: QualityStatus
    reason_code: str
    summary: str
    available_at: datetime | None
    reference_hash: str | None
    evidence: QualityEvidence = field(default_factory=QualityEvidence.from_mapping)
    expected_observation: bool | None = None

    def __post_init__(self) -> None:
        if self.status not in {QualityStatus.PASS, QualityStatus.FAIL, QualityStatus.UNKNOWN}:
            raise DataQualityError("reference.status 只能是 PASS、FAIL 或 UNKNOWN")
        reason_code = _required_text(self.reason_code, "reference.reason_code")
        if _REASON_CODE_RE.fullmatch(reason_code) is None:
            raise DataQualityError("reference.reason_code 必须是大写稳定原因码")
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "summary", _required_text(self.summary, "reference.summary"))
        if self.available_at is None:
            if self.status is not QualityStatus.UNKNOWN:
                raise DataQualityError("非 UNKNOWN reference 必须提供 available_at")
        else:
            object.__setattr__(
                self,
                "available_at",
                _utc_datetime(self.available_at, "reference.available_at"),
            )
        if not isinstance(self.evidence, QualityEvidence):
            raise DataQualityError("reference.evidence 必须是 QualityEvidence")
        if self.reference_hash is None:
            if self.status is not QualityStatus.UNKNOWN or self.evidence.canonical_json != "{}":
                raise DataQualityError(
                    "没有 immutable reference_hash 的结论只能是无证据 UNKNOWN"
                )
        elif not isinstance(self.reference_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.reference_hash
        ):
            raise DataQualityError("reference.reference_hash 必须是 SHA-256 或 None")
        if self.expected_observation is not None and type(self.expected_observation) is not bool:
            raise DataQualityError("reference.expected_observation 必须是 bool 或 None")


class CalendarConsistencyResolver(Protocol):
    """仅提供可审计日历事实，不依赖 application 或交易执行层。"""

    def assess_calendar_consistency(
        self,
        *,
        frame: pl.DataFrame,
        artifact: Artifact,
        decision_at: datetime,
    ) -> QualityReferenceDecision:
        ...


class ContractConsistencyResolver(Protocol):
    """仅提供可审计合约事实，不依赖 application 或交易执行层。"""

    def assess_contract_consistency(
        self,
        *,
        frame: pl.DataFrame,
        artifact: Artifact,
        decision_at: datetime,
    ) -> QualityReferenceDecision:
        ...


class CalendarCoverageResolver(Protocol):
    """为一段数据间隔说明是否应存在观测；供 gap 规则做日历感知判断。"""

    def assess_expected_observation(
        self,
        *,
        start: datetime,
        end: datetime,
        artifact: Artifact,
        decision_at: datetime,
    ) -> QualityReferenceDecision:
        ...


@dataclass(frozen=True, slots=True)
class SchemaField:
    """预期字段及其稳定 Polars dtype 名称。"""

    name: str
    dtype: str
    nullable: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "schema_field.name"))
        object.__setattr__(self, "dtype", _required_text(self.dtype, "schema_field.dtype"))
        if type(self.nullable) is not bool:
            raise DataQualityError("schema_field.nullable 必须是 bool")


@dataclass(frozen=True, slots=True)
class CompletenessRule:
    """最少行数和必填字段的空值比例限制。"""

    required_columns: tuple[str, ...]
    min_rows: int
    max_null_fraction: float

    def __post_init__(self) -> None:
        columns = _canonical_columns(self.required_columns, "completeness.required_columns")
        if not isinstance(self.min_rows, int) or isinstance(self.min_rows, bool) or self.min_rows < 0:
            raise DataQualityError("completeness.min_rows 必须是非负整数")
        fraction = _finite_number(self.max_null_fraction, "completeness.max_null_fraction")
        if not 0 <= fraction <= 1:
            raise DataQualityError("completeness.max_null_fraction 必须在 [0, 1]")
        object.__setattr__(self, "required_columns", columns)
        object.__setattr__(self, "max_null_fraction", fraction)


@dataclass(frozen=True, slots=True)
class UniquenessRule:
    """显式主键唯一性规则。"""

    primary_key: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "primary_key", _canonical_columns(self.primary_key, "uniqueness.primary_key"))


@dataclass(frozen=True, slots=True)
class OrderingRule:
    """要求输入原始顺序已排序；引擎不会先排序再检查。"""

    order_by: tuple[str, ...]
    group_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_by", _canonical_columns(self.order_by, "ordering.order_by"))
        object.__setattr__(self, "group_by", _canonical_columns_allow_empty(self.group_by, "ordering.group_by"))
        overlap = set(self.order_by).intersection(self.group_by)
        if overlap:
            raise DataQualityError("ordering.order_by 与 group_by 不能重叠")


@dataclass(frozen=True, slots=True)
class RangeRule:
    """某个数值字段的闭区间规则。"""

    column: str
    minimum: float | None
    maximum: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "column", _required_text(self.column, "range.column"))
        minimum = None if self.minimum is None else _finite_number(self.minimum, "range.minimum")
        maximum = None if self.maximum is None else _finite_number(self.maximum, "range.maximum")
        if minimum is None and maximum is None:
            raise DataQualityError("range.minimum 与 maximum 至少需要一个")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise DataQualityError("range.minimum 不能大于 maximum")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)


@dataclass(frozen=True, slots=True)
class StalenessRule:
    """基于制品 acquired_at 与显式 decision_at 的时效规则。"""

    warn_after: timedelta | None
    fail_after: timedelta

    def __post_init__(self) -> None:
        warn_after = (
            None
            if self.warn_after is None
            else _non_negative_timedelta(self.warn_after, "staleness.warn_after")
        )
        fail_after = _non_negative_timedelta(self.fail_after, "staleness.fail_after")
        if warn_after is not None and warn_after >= fail_after:
            raise DataQualityError("staleness.warn_after 必须早于 fail_after")
        object.__setattr__(self, "warn_after", warn_after)
        object.__setattr__(self, "fail_after", fail_after)


@dataclass(frozen=True, slots=True)
class GapRule:
    """日历覆盖窗口内的最大观测间隔。

    没有 ``coverage_start``/``coverage_end`` 时，核心不会猜测首尾边界，而是返回 UNKNOWN。
    """

    timestamp_column: str
    maximum_gap: timedelta
    group_by: tuple[str, ...] = ()
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_column", _required_text(self.timestamp_column, "gap.timestamp_column"))
        maximum_gap = _non_negative_timedelta(self.maximum_gap, "gap.maximum_gap")
        if maximum_gap == timedelta(0):
            raise DataQualityError("gap.maximum_gap 必须大于零")
        object.__setattr__(self, "maximum_gap", maximum_gap)
        object.__setattr__(self, "group_by", _canonical_columns_allow_empty(self.group_by, "gap.group_by"))
        if self.timestamp_column in self.group_by:
            raise DataQualityError("gap.timestamp_column 不能同时出现在 group_by")
        if (self.coverage_start is None) != (self.coverage_end is None):
            raise DataQualityError("gap.coverage_start 与 coverage_end 必须同时提供或同时省略")
        if self.coverage_start is not None and self.coverage_end is not None:
            coverage_start = _utc_datetime(self.coverage_start, "gap.coverage_start")
            coverage_end = _utc_datetime(self.coverage_end, "gap.coverage_end")
            if coverage_start >= coverage_end:
                raise DataQualityError("gap.coverage_start 必须早于 coverage_end")
            object.__setattr__(self, "coverage_start", coverage_start)
            object.__setattr__(self, "coverage_end", coverage_end)


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    """某一主键在不可变基线中的规范内容摘要。"""

    key_json: str
    content_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.key_json, str) or not self.key_json:
            raise DataQualityError("revision_record.key_json 必须是非空 canonical JSON")
        try:
            key = json.loads(self.key_json)
        except json.JSONDecodeError as exc:
            raise DataQualityError("revision_record.key_json 必须是 JSON") from exc
        normalized = _canonical_json_value(key, "revision_record.key")
        if not isinstance(normalized, dict):
            raise DataQualityError("revision_record.key 必须是 JSON 映射")
        canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if canonical != self.key_json:
            raise DataQualityError("revision_record.key_json 必须是 canonical JSON")
        if not isinstance(self.content_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.content_hash):
            raise DataQualityError("revision_record.content_hash 必须是 SHA-256")


@dataclass(frozen=True, slots=True)
class RevisionBaseline:
    """显式 prior immutable baseline；没有它时 revision 只能为 UNKNOWN。"""

    artifact_snapshot: ArtifactSnapshot
    key_columns: tuple[str, ...]
    content_columns: tuple[str, ...]
    records: tuple[RevisionRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_snapshot, ArtifactSnapshot):
            raise DataQualityError("revision_baseline.artifact_snapshot 必须是 ArtifactSnapshot")
        object.__setattr__(self, "key_columns", _canonical_columns(self.key_columns, "revision_baseline.key_columns"))
        object.__setattr__(
            self,
            "content_columns",
            _canonical_columns(self.content_columns, "revision_baseline.content_columns"),
        )
        if set(self.key_columns).intersection(self.content_columns):
            raise DataQualityError("revision_baseline.key_columns 与 content_columns 不能重叠")
        records = tuple(self.records)
        if not all(isinstance(item, RevisionRecord) for item in records):
            raise DataQualityError("revision_baseline.records 必须全部是 RevisionRecord")
        keys = tuple(item.key_json for item in records)
        if len(keys) != len(set(keys)):
            raise DataQualityError("revision_baseline.records 不能包含重复主键")
        object.__setattr__(self, "records", tuple(sorted(records, key=lambda item: item.key_json)))

    @classmethod
    def from_frame(
        cls,
        *,
        artifact: Artifact,
        frame: pl.DataFrame,
        key_columns: tuple[str, ...],
        content_columns: tuple[str, ...],
    ) -> "RevisionBaseline":
        """从一个显式不可变制品及其表格内容构造 prior baseline。"""

        if not isinstance(frame, pl.DataFrame):
            raise DataQualityError("revision_baseline.frame 必须是 Polars DataFrame")
        try:
            ArtifactSnapshot.from_artifact(artifact)
        except DataDomainError as exc:
            raise DataQualityError(str(exc)) from exc
        if content_sha256(canonical_frame_payload(frame), field_name="revision_baseline.frame") != (
            artifact.metadata.content_hash
        ):
            raise DataQualityError(
                "revision_baseline.frame 的 canonical payload 必须精确匹配 artifact.content_hash"
            )
        keys = _canonical_columns(key_columns, "revision_baseline.key_columns")
        contents = _canonical_columns(content_columns, "revision_baseline.content_columns")
        missing = [column for column in (*keys, *contents) if column not in frame.columns]
        if missing:
            raise DataQualityError("revision_baseline.frame 缺少字段: " + ", ".join(missing))
        records = tuple(
            RevisionRecord(
                key_json=_canonical_row_json(row, keys, "revision_baseline.key"),
                content_hash=canonical_json_sha256(
                    _canonical_row_mapping(row, contents, "revision_baseline.content")
                ),
            )
            for row in frame.select([*keys, *contents]).iter_rows(named=True)
        )
        return cls(
            artifact_snapshot=ArtifactSnapshot.from_artifact(artifact),
            key_columns=keys,
            content_columns=contents,
            records=records,
        )

    @property
    def fingerprint(self) -> str:
        """把基线制品快照与逐行摘要一起纳入规则策略身份。"""

        return canonical_json_sha256(
            {
                "artifact_snapshot": self.artifact_snapshot.snapshot_hash,
                "content_columns": self.content_columns,
                "key_columns": self.key_columns,
                "records": [
                    {"content_hash": item.content_hash, "key_json": item.key_json}
                    for item in self.records
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class RevisionRule:
    """通过 prior immutable baseline 检查同主键内容修订。

    本规则只在制品级 ``available_at`` 上做 PIT 判定；逐行 ``available_at`` 与版本选择属于
    P1-WP07，当前没有被声称为已解决。
    """

    key_columns: tuple[str, ...]
    content_columns: tuple[str, ...]
    on_change_status: QualityStatus
    baseline: RevisionBaseline | None

    def __post_init__(self) -> None:
        keys = _canonical_columns(self.key_columns, "revision.key_columns")
        contents = _canonical_columns(self.content_columns, "revision.content_columns")
        if set(keys).intersection(contents):
            raise DataQualityError("revision.key_columns 与 content_columns 不能重叠")
        if self.on_change_status not in {QualityStatus.WARN, QualityStatus.FAIL}:
            raise DataQualityError("revision.on_change_status 只能是 WARN 或 FAIL")
        if self.baseline is not None:
            if not isinstance(self.baseline, RevisionBaseline):
                raise DataQualityError("revision.baseline 必须是 RevisionBaseline 或 None")
            if self.baseline.key_columns != keys or self.baseline.content_columns != contents:
                raise DataQualityError("revision.baseline 的 key/content 字段必须与规则精确一致")
        object.__setattr__(self, "key_columns", keys)
        object.__setattr__(self, "content_columns", contents)


@dataclass(frozen=True, slots=True)
class QualityRequest:
    """一次纯内存、显式 PIT 的候选制品质量评估请求。"""

    artifact: Artifact
    frame: pl.DataFrame
    checked_at: datetime
    decision_at: datetime
    completeness: CompletenessRule
    uniqueness: UniquenessRule
    ordering: OrderingRule
    schema: tuple[SchemaField, ...]
    expected_artifact_schema_version: str
    allow_additional_columns: bool
    ranges: tuple[RangeRule, ...]
    staleness: StalenessRule
    gap: GapRule
    revision: RevisionRule
    policy_id: str
    policy_version: str
    evaluated_payload: bytes
    calendar_resolver: CalendarConsistencyResolver | None = None
    contract_resolver: ContractConsistencyResolver | None = None
    calendar_coverage_resolver: CalendarCoverageResolver | None = None
    calendar_resolver_identity: str | None = None
    contract_resolver_identity: str | None = None
    calendar_coverage_resolver_identity: str | None = None
    critical_rules: frozenset[QualityRule] = field(default_factory=frozenset)
    policy_hash: str = field(init=False)
    frame_hash: str = field(init=False)
    evaluated_payload_hash: str = field(init=False)

    def __post_init__(self) -> None:
        # Artifact 是 Protocol，DataQualityResult 会负责作最终具体类型验证；这里借用其
        # snapshot 工厂，确保请求不能携带伪造对象。
        try:
            ArtifactSnapshot.from_artifact(self.artifact)
        except DataDomainError as exc:
            raise DataQualityError(str(exc)) from exc
        if not isinstance(self.frame, pl.DataFrame):
            raise DataQualityError("quality.frame 必须是 Polars DataFrame")
        # frozen dataclass 不会让 Polars 的底层对象不可变；保存独立副本，并在 Engine 入口再次
        # 校验它，防止调用方在构造请求后替换单元格却继续复用旧 artifact 身份。
        frame = self.frame.clone()
        object.__setattr__(self, "frame", frame)
        if not isinstance(self.evaluated_payload, bytes):
            raise DataQualityError("quality.evaluated_payload 必须是 canonical frame bytes")
        canonical_payload = canonical_frame_payload(frame)
        if self.evaluated_payload != canonical_payload:
            raise DataQualityError(
                "quality.evaluated_payload 必须与引擎重建的 canonical frame bytes 逐字节一致"
            )
        evaluated_payload_hash = content_sha256(
            self.evaluated_payload,
            field_name="quality.evaluated_payload",
        )
        if evaluated_payload_hash != self.artifact.metadata.content_hash:
            raise DataQualityError(
                "quality.evaluated_payload 的内容哈希必须精确匹配 artifact.content_hash"
            )
        object.__setattr__(self, "frame_hash", evaluated_payload_hash)
        object.__setattr__(self, "evaluated_payload_hash", evaluated_payload_hash)
        checked_at = _utc_datetime(self.checked_at, "quality.checked_at")
        decision_at = _utc_datetime(self.decision_at, "quality.decision_at")
        artifact_metadata = self.artifact.metadata
        for field_name, value in (("quality.checked_at", checked_at), ("quality.decision_at", decision_at)):
            if value < artifact_metadata.acquired_at:
                raise DataQualityError(f"{field_name} 不能早于 artifact.acquired_at")
            if value > artifact_metadata.available_at:
                raise DataQualityError(f"{field_name} 不能晚于 artifact.available_at")
        object.__setattr__(self, "checked_at", checked_at)
        object.__setattr__(self, "decision_at", decision_at)
        if not isinstance(self.completeness, CompletenessRule):
            raise DataQualityError("quality.completeness 必须是 CompletenessRule")
        if not isinstance(self.uniqueness, UniquenessRule):
            raise DataQualityError("quality.uniqueness 必须是 UniquenessRule")
        if not isinstance(self.ordering, OrderingRule):
            raise DataQualityError("quality.ordering 必须是 OrderingRule")
        fields = tuple(self.schema)
        if not fields or not all(isinstance(item, SchemaField) for item in fields):
            raise DataQualityError("quality.schema 必须是非空 SchemaField 序列")
        names = tuple(item.name for item in fields)
        if len(names) != len(set(names)):
            raise DataQualityError("quality.schema 不能包含重复字段")
        object.__setattr__(self, "schema", tuple(sorted(fields, key=lambda item: item.name)))
        object.__setattr__(
            self,
            "expected_artifact_schema_version",
            _required_text(
                self.expected_artifact_schema_version,
                "quality.expected_artifact_schema_version",
            ),
        )
        if type(self.allow_additional_columns) is not bool:
            raise DataQualityError("quality.allow_additional_columns 必须是 bool")
        ranges = tuple(self.ranges)
        if not all(isinstance(item, RangeRule) for item in ranges):
            raise DataQualityError("quality.ranges 必须全部是 RangeRule")
        if len({item.column for item in ranges}) != len(ranges):
            raise DataQualityError("quality.ranges 不能对同一字段重复声明")
        object.__setattr__(self, "ranges", tuple(sorted(ranges, key=lambda item: item.column)))
        if not isinstance(self.staleness, StalenessRule):
            raise DataQualityError("quality.staleness 必须是 StalenessRule")
        if not isinstance(self.gap, GapRule):
            raise DataQualityError("quality.gap 必须是 GapRule")
        if not isinstance(self.revision, RevisionRule):
            raise DataQualityError("quality.revision 必须是 RevisionRule")
        policy_id = _required_text(self.policy_id, "quality.policy_id")
        policy_version = _required_text(self.policy_version, "quality.policy_version")
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "policy_version", policy_version)
        calendar_identity = _validate_resolver_identity(
            resolver=self.calendar_resolver,
            identity=self.calendar_resolver_identity,
            field_name="quality.calendar_resolver_identity",
        )
        contract_identity = _validate_resolver_identity(
            resolver=self.contract_resolver,
            identity=self.contract_resolver_identity,
            field_name="quality.contract_resolver_identity",
        )
        coverage_identity = _validate_resolver_identity(
            resolver=self.calendar_coverage_resolver,
            identity=self.calendar_coverage_resolver_identity,
            field_name="quality.calendar_coverage_resolver_identity",
        )
        object.__setattr__(self, "calendar_resolver_identity", calendar_identity)
        object.__setattr__(self, "contract_resolver_identity", contract_identity)
        object.__setattr__(self, "calendar_coverage_resolver_identity", coverage_identity)
        critical_rules = frozenset(self.critical_rules)
        if not critical_rules or not all(isinstance(item, QualityRule) for item in critical_rules):
            raise DataQualityError("quality.critical_rules 必须是非空 QualityRule 集合")
        object.__setattr__(self, "critical_rules", critical_rules)
        object.__setattr__(
            self,
            "policy_hash",
            canonical_json_sha256(
                {
                    "calendar_coverage_resolver_identity": coverage_identity,
                    "calendar_resolver_identity": calendar_identity,
                    "completeness": {
                        "max_null_fraction": self.completeness.max_null_fraction,
                        "min_rows": self.completeness.min_rows,
                        "required_columns": self.completeness.required_columns,
                    },
                    "contract_resolver_identity": contract_identity,
                    "critical_rules": sorted(item.value for item in critical_rules),
                    "gap": {
                        "coverage_end": None
                        if self.gap.coverage_end is None
                        else self.gap.coverage_end.isoformat(),
                        "coverage_start": None
                        if self.gap.coverage_start is None
                        else self.gap.coverage_start.isoformat(),
                        "group_by": self.gap.group_by,
                        "maximum_gap_seconds": self.gap.maximum_gap.total_seconds(),
                        "timestamp_column": self.gap.timestamp_column,
                    },
                    "ordering": {
                        "group_by": self.ordering.group_by,
                        "order_by": self.ordering.order_by,
                    },
                    "policy_id": policy_id,
                    "policy_version": policy_version,
                    "ranges": [
                        {
                            "column": item.column,
                            "maximum": item.maximum,
                            "minimum": item.minimum,
                        }
                        for item in self.ranges
                    ],
                    "revision": {
                        "baseline": None
                        if self.revision.baseline is None
                        else self.revision.baseline.fingerprint,
                        "content_columns": self.revision.content_columns,
                        "key_columns": self.revision.key_columns,
                        "on_change_status": self.revision.on_change_status.value,
                    },
                    "schema": [
                        {"dtype": item.dtype, "name": item.name, "nullable": item.nullable}
                        for item in self.schema
                    ],
                    "schema_policy": {
                        "allow_additional_columns": self.allow_additional_columns,
                        "expected_artifact_schema_version": self.expected_artifact_schema_version,
                    },
                    "staleness": {
                        "fail_after_seconds": self.staleness.fail_after.total_seconds(),
                        "warn_after_seconds": None
                        if self.staleness.warn_after is None
                        else self.staleness.warn_after.total_seconds(),
                    },
                    "uniqueness": {"primary_key": self.uniqueness.primary_key},
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class QualityEvaluation:
    """候选制品的预发布质量报告。

    此对象不改变 artifact metadata，也不自动把失败结论写入已声明 PASS 的制品。只有调用
    ``bind_published_artifact`` 且重构后的制品身份与聚合状态完全匹配时，才能产生既有
    ``DataQualityResult`` 审计记录。
    """

    artifact: Artifact
    checked_at: datetime
    decision_at: datetime
    findings: tuple[QualityFinding, ...]
    critical_rules: frozenset[QualityRule]
    policy_hash: str
    frame_hash: str
    evaluated_payload_hash: str
    evaluation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            ArtifactSnapshot.from_artifact(self.artifact)
        except DataDomainError as exc:
            raise DataQualityError(str(exc)) from exc
        checked_at = _utc_datetime(self.checked_at, "evaluation.checked_at")
        decision_at = _utc_datetime(self.decision_at, "evaluation.decision_at")
        for field_name, value in (("evaluation.checked_at", checked_at), ("evaluation.decision_at", decision_at)):
            if value < self.artifact.metadata.acquired_at:
                raise DataQualityError(f"{field_name} 不能早于 artifact.acquired_at")
            if value > self.artifact.metadata.available_at:
                raise DataQualityError(f"{field_name} 不能晚于 artifact.available_at")
        findings = tuple(self.findings)
        if {item.rule for item in findings} != set(QualityRule):
            raise DataQualityError("evaluation.findings 必须恰好覆盖全部 10 条 QualityRule")
        if not all(isinstance(item, QualityFinding) for item in findings):
            raise DataQualityError("evaluation.findings 必须全部是 QualityFinding")
        canonical_findings = tuple(sorted(findings, key=lambda item: item.rule.value))
        critical_rules = frozenset(self.critical_rules)
        if not critical_rules or not critical_rules <= set(QualityRule):
            raise DataQualityError("evaluation.critical_rules 必须是非空 QualityRule 集合")
        object.__setattr__(self, "checked_at", checked_at)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "findings", canonical_findings)
        object.__setattr__(self, "critical_rules", critical_rules)
        if not isinstance(self.policy_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.policy_hash):
            raise DataQualityError("evaluation.policy_hash 必须是 SHA-256")
        for hash_field_name, hash_value in (
            ("evaluation.frame_hash", self.frame_hash),
            ("evaluation.evaluated_payload_hash", self.evaluated_payload_hash),
        ):
            if not isinstance(hash_value, str) or not re.fullmatch(r"[0-9a-f]{64}", hash_value):
                raise DataQualityError(f"{hash_field_name} 必须是 SHA-256")
        if self.frame_hash != self.evaluated_payload_hash:
            raise DataQualityError("evaluation.frame_hash 必须等于 evaluated_payload_hash")
        if self.evaluated_payload_hash != self.artifact.metadata.content_hash:
            raise DataQualityError(
                "evaluation.evaluated_payload_hash 必须精确匹配 artifact.content_hash"
            )
        object.__setattr__(
            self,
            "evaluation_hash",
            canonical_json_sha256(
                {
                    "artifact": ArtifactSnapshot.from_artifact(self.artifact).snapshot_hash,
                    "checked_at": checked_at.isoformat(),
                    "critical_rules": sorted(item.value for item in critical_rules),
                    "decision_at": decision_at.isoformat(),
                    "findings": [item.fingerprint for item in canonical_findings],
                    "frame_hash": self.frame_hash,
                    "policy_hash": self.policy_hash,
                }
            ),
        )

    @property
    def aggregate_status(self) -> QualityStatus:
        return max((item.status for item in self.findings), key=lambda item: _QUALITY_PRECEDENCE[item])

    def finding_for(self, rule: QualityRule) -> QualityFinding:
        if not isinstance(rule, QualityRule):
            raise DataQualityError("rule 必须是 QualityRule")
        return next(item for item in self.findings if item.rule is rule)

    def require_eligible(
        self,
        *,
        mode: QualityMode | str,
        allow_warn: bool,
        allow_unknown_for_noncritical: bool,
    ) -> None:
        """按显式下游策略执行质量门禁。

        FAIL 一律拒绝；critical rule 的 UNKNOWN 一律拒绝。WARN 与非关键 UNKNOWN 绝不使用
        隐式阈值，调用方必须明确传入许可策略。
        """

        try:
            normalized_mode = QualityMode(mode)
        except (TypeError, ValueError) as exc:
            raise DataQualityError("mode 必须是 research 或 production") from exc
        if type(allow_warn) is not bool or type(allow_unknown_for_noncritical) is not bool:
            raise DataQualityError("质量门禁策略必须显式使用 bool")
        failures = [item for item in self.findings if item.status is QualityStatus.FAIL]
        if failures:
            raise DataQualityError(
                f"{normalized_mode.value} 质量门禁拒绝 FAIL："
                + ", ".join(item.rule.value for item in failures)
            )
        critical_unknown = [
            item
            for item in self.findings
            if item.rule in self.critical_rules and item.status is QualityStatus.UNKNOWN
        ]
        if critical_unknown:
            raise DataQualityError(
                f"{normalized_mode.value} 质量门禁拒绝关键 UNKNOWN："
                + ", ".join(item.rule.value for item in critical_unknown)
            )
        warnings = [item for item in self.findings if item.status is QualityStatus.WARN]
        if warnings and not allow_warn:
            raise DataQualityError(
                f"{normalized_mode.value} 质量门禁未授权 WARN："
                + ", ".join(item.rule.value for item in warnings)
            )
        unknown = [item for item in self.findings if item.status is QualityStatus.UNKNOWN]
        if unknown and not allow_unknown_for_noncritical:
            raise DataQualityError(
                f"{normalized_mode.value} 质量门禁未授权 UNKNOWN："
                + ", ".join(item.rule.value for item in unknown)
            )
        if self.artifact.metadata.quality_status in {QualityStatus.FAIL, QualityStatus.UNKNOWN}:
            raise DataQualityError(
                f"{normalized_mode.value} 质量门禁拒绝 artifact metadata="
                f"{self.artifact.metadata.quality_status.value}"
            )
        if self.artifact.metadata.quality_status is QualityStatus.WARN and not allow_warn:
            raise DataQualityError(
                f"{normalized_mode.value} 质量门禁未授权 artifact metadata=WARN"
            )

    def bind_published_artifact(self, published_artifact: Artifact) -> tuple[DataQualityResult, ...]:
        """将报告绑定到重构后的待发布制品，并生成既有 ``DataQualityResult`` 记录。

        重构只能把候选制品的 metadata 质量保持或变得更保守，不能以本次 finding
        较好为由把既有 ``WARN``/``UNKNOWN``/``FAIL`` 提升为较弱状态。这避免绕过
        ``DataQualityResult`` 对已发布 artifact 的质量语义约束。
        """

        _assert_same_publishable_identity(self.artifact, published_artifact)
        candidate_status = self.artifact.metadata.quality_status
        published_status = published_artifact.metadata.quality_status
        if _QUALITY_PRECEDENCE[published_status] < _QUALITY_PRECEDENCE[candidate_status]:
            raise DataQualityError(
                "待发布 artifact.metadata.quality_status 不得优于候选 artifact metadata："
                f"candidate={candidate_status.value}, published={published_status.value}"
            )
        if published_artifact.metadata.quality_status is not self.aggregate_status:
            raise DataQualityError(
                "待发布 artifact.metadata.quality_status 必须精确等于预发布 assessment 聚合结论"
            )
        results: list[DataQualityResult] = []
        for finding in self.findings:
            try:
                results.append(
                    DataQualityResult(
                        artifact=published_artifact,
                        check_id=f"quality.{finding.rule.value}",
                        quality_status=finding.status,
                        checked_at=self.checked_at,
                        summary=f"{finding.reason_code}: {finding.summary}",
                    )
                )
            except DataDomainError as exc:
                raise DataQualityError("待发布 artifact 与质量结论不兼容") from exc
        return tuple(results)

    def to_data_quality_results(self, published_artifact: Artifact) -> tuple[DataQualityResult, ...]:
        """兼容的显式转换入口；等同于 ``bind_published_artifact``。"""

        return self.bind_published_artifact(published_artifact)


@dataclass(frozen=True, slots=True)
class PublishedQualityAssessment:
    """一份绑定到最终制品快照的不可变质量证据。

    ``QualityEvaluation`` 是候选制品的纯内存报告；发布前可能因聚合结论将候选
    ``quality_status`` 收紧。此对象把候选与最终快照、完整十项 findings 和策略身份一并
    冻结，供不可变制品库追加式保存。它不写文件，也不替代发布器对质量引擎的实际调用。
    """

    candidate_snapshot_hash: str
    published_snapshot_hash: str
    evaluation_hash: str
    policy_hash: str
    checked_at: datetime
    decision_at: datetime
    findings: tuple[QualityFinding, ...]
    critical_rules: frozenset[QualityRule]
    frame_hash: str
    aggregate_status: QualityStatus
    assessment_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "candidate_snapshot_hash",
            "published_snapshot_hash",
            "evaluation_hash",
            "policy_hash",
            "frame_hash",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise DataQualityError(f"assessment.{field_name} 必须是 SHA-256")
        checked_at = _utc_datetime(self.checked_at, "assessment.checked_at")
        decision_at = _utc_datetime(self.decision_at, "assessment.decision_at")
        findings = tuple(self.findings)
        if {item.rule for item in findings} != set(QualityRule):
            raise DataQualityError("assessment.findings 必须恰好覆盖全部 10 条 QualityRule")
        if not all(isinstance(item, QualityFinding) for item in findings):
            raise DataQualityError("assessment.findings 必须全部是 QualityFinding")
        canonical_findings = tuple(sorted(findings, key=lambda item: item.rule.value))
        critical_rules = frozenset(self.critical_rules)
        if not critical_rules or not critical_rules <= set(QualityRule):
            raise DataQualityError("assessment.critical_rules 必须是非空 QualityRule 集合")
        if not isinstance(self.aggregate_status, QualityStatus):
            raise DataQualityError("assessment.aggregate_status 必须是 QualityStatus")
        actual_status = max(
            (item.status for item in canonical_findings), key=lambda item: _QUALITY_PRECEDENCE[item]
        )
        if self.aggregate_status is not actual_status:
            raise DataQualityError("assessment.aggregate_status 必须等于 findings 聚合状态")
        object.__setattr__(self, "checked_at", checked_at)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "findings", canonical_findings)
        object.__setattr__(self, "critical_rules", critical_rules)
        object.__setattr__(
            self,
            "assessment_hash",
            canonical_json_sha256(self.as_mapping()),
        )

    @classmethod
    def from_evaluation(
        cls,
        *,
        evaluation: QualityEvaluation,
        published_artifact: Artifact,
    ) -> "PublishedQualityAssessment":
        """将真实引擎结果绑定到最终制品，拒绝身份或质量状态偷换。"""

        if not isinstance(evaluation, QualityEvaluation):
            raise DataQualityError("evaluation 必须是 QualityEvaluation")
        # 这里必须执行既有的 publish-time 全量校验；不能只比较两个 hash 字段。
        evaluation.bind_published_artifact(published_artifact)
        return cls(
            candidate_snapshot_hash=ArtifactSnapshot.from_artifact(evaluation.artifact).snapshot_hash,
            published_snapshot_hash=ArtifactSnapshot.from_artifact(published_artifact).snapshot_hash,
            evaluation_hash=evaluation.evaluation_hash,
            policy_hash=evaluation.policy_hash,
            checked_at=evaluation.checked_at,
            decision_at=evaluation.decision_at,
            findings=evaluation.findings,
            critical_rules=evaluation.critical_rules,
            frame_hash=evaluation.frame_hash,
            aggregate_status=evaluation.aggregate_status,
        )

    def as_mapping(self) -> dict[str, object]:
        """返回唯一、无密钥的 JSON 记录体；调用方可安全写入追加式存储。"""

        return {
            "aggregate_status": self.aggregate_status.value,
            "candidate_snapshot_hash": self.candidate_snapshot_hash,
            "checked_at": self.checked_at.isoformat(),
            "critical_rules": sorted(item.value for item in self.critical_rules),
            "decision_at": self.decision_at.isoformat(),
            "evaluation_hash": self.evaluation_hash,
            "findings": [
                {
                    "evidence": item.evidence.as_mapping(),
                    "reason_code": item.reason_code,
                    "rule": item.rule.value,
                    "status": item.status.value,
                    "summary": item.summary,
                }
                for item in self.findings
            ],
            "frame_hash": self.frame_hash,
            "policy_hash": self.policy_hash,
            "published_snapshot_hash": self.published_snapshot_hash,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "PublishedQualityAssessment":
        """从制品库读取的 canonical JSON 重建并重新验证质量证据。"""

        expected = {
            "aggregate_status",
            "candidate_snapshot_hash",
            "checked_at",
            "critical_rules",
            "decision_at",
            "evaluation_hash",
            "findings",
            "frame_hash",
            "policy_hash",
            "published_snapshot_hash",
        }
        if set(payload) != expected:
            raise DataQualityError("assessment 记录字段不完整或包含未知字段")
        raw_findings = payload["findings"]
        if not isinstance(raw_findings, list):
            raise DataQualityError("assessment.findings 必须是列表")
        findings: list[QualityFinding] = []
        for index, raw_finding in enumerate(raw_findings):
            if not isinstance(raw_finding, Mapping) or set(raw_finding) != {
                "evidence",
                "reason_code",
                "rule",
                "status",
                "summary",
            }:
                raise DataQualityError(f"assessment.findings[{index}] 字段不完整或包含未知字段")
            evidence = raw_finding["evidence"]
            if not isinstance(evidence, Mapping):
                raise DataQualityError(f"assessment.findings[{index}].evidence 必须是映射")
            try:
                findings.append(
                    QualityFinding(
                        rule=QualityRule(_required_text(raw_finding["rule"], "assessment.rule")),
                        status=QualityStatus(
                            _required_text(raw_finding["status"], "assessment.status")
                        ),
                        reason_code=_required_text(
                            raw_finding["reason_code"], "assessment.reason_code"
                        ),
                        summary=_required_text(raw_finding["summary"], "assessment.summary"),
                        evidence=QualityEvidence.from_mapping(evidence),
                    )
                )
            except ValueError as exc:
                raise DataQualityError(f"assessment.findings[{index}] 枚举值无效") from exc
        raw_critical = payload["critical_rules"]
        if not isinstance(raw_critical, list):
            raise DataQualityError("assessment.critical_rules 必须是列表")
        try:
            critical_rules = frozenset(
                QualityRule(_required_text(item, "assessment.critical_rules"))
                for item in raw_critical
            )
            checked_at = datetime.fromisoformat(
                _required_text(payload["checked_at"], "assessment.checked_at")
            )
            decision_at = datetime.fromisoformat(
                _required_text(payload["decision_at"], "assessment.decision_at")
            )
            return cls(
                candidate_snapshot_hash=_required_text(
                    payload["candidate_snapshot_hash"], "assessment.candidate_snapshot_hash"
                ),
                published_snapshot_hash=_required_text(
                    payload["published_snapshot_hash"], "assessment.published_snapshot_hash"
                ),
                evaluation_hash=_required_text(payload["evaluation_hash"], "assessment.evaluation_hash"),
                policy_hash=_required_text(payload["policy_hash"], "assessment.policy_hash"),
                checked_at=checked_at,
                decision_at=decision_at,
                findings=tuple(findings),
                critical_rules=critical_rules,
                frame_hash=_required_text(payload["frame_hash"], "assessment.frame_hash"),
                aggregate_status=QualityStatus(
                    _required_text(payload["aggregate_status"], "assessment.aggregate_status")
                ),
            )
        except ValueError as exc:
            raise DataQualityError("assessment 时间或枚举值无效") from exc


def _canonical_columns(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    result = _canonical_columns_allow_empty(values, field_name)
    if not result:
        raise DataQualityError(f"{field_name} 不能为空")
    return result


def _canonical_columns_allow_empty(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    try:
        result = tuple(_required_text(value, field_name) for value in values)
    except TypeError as exc:
        raise DataQualityError(f"{field_name} 必须是文本序列") from exc
    if len(result) != len(set(result)):
        raise DataQualityError(f"{field_name} 不能包含重复字段")
    return result


def _canonical_row_mapping(
    row: Mapping[str, object],
    columns: tuple[str, ...],
    field_name: str,
) -> dict[str, object]:
    return {
        column: _canonical_cell(row[column], f"{field_name}.{column}")
        for column in columns
    }


def _canonical_row_json(
    row: Mapping[str, object], columns: tuple[str, ...], field_name: str
) -> str:
    return json.dumps(
        _canonical_row_mapping(row, columns, field_name),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_cell(value: object, field_name: str) -> object:
    if isinstance(value, datetime):
        return _utc_datetime(value, field_name).isoformat()
    # date、Decimal、Polars scalar 等可以用稳定文本表达；绝不接受 list/dict，避免未审计
    # 的嵌套可变数据进入 revision identity。
    if value is None or isinstance(value, (bool, int, float, str)):
        return _canonical_json_value(value, field_name)
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return _required_text(isoformat(), field_name)
    return _required_text(str(value), field_name)


def _assert_same_publishable_identity(candidate: Artifact, published: Artifact) -> None:
    """质量重构只能改 quality_status，不能偷换内容、来源、schema 或 PIT。"""

    candidate_snapshot = ArtifactSnapshot.from_artifact(candidate)
    published_snapshot = ArtifactSnapshot.from_artifact(published)
    fields = (
        "artifact_id",
        "kind",
        "source_id",
        "content_hash",
        "acquired_at",
        "available_at",
        "schema_version",
        "transform_version",
        "provenance",
    )
    mismatches = [
        field_name
        for field_name in fields
        if getattr(candidate_snapshot, field_name) != getattr(published_snapshot, field_name)
    ]
    if mismatches:
        raise DataQualityError("待发布 artifact 与候选 assessment 身份不一致：" + ", ".join(mismatches))


def _validate_resolver_identity(
    *, resolver: object | None, identity: str | None, field_name: str
) -> str | None:
    if resolver is None:
        if identity is not None:
            raise DataQualityError(f"{field_name} 只有注入 resolver 时才能提供")
        return None
    if identity is None:
        raise DataQualityError(f"{field_name} 必须显式提供稳定 resolver identity")
    return _required_text(identity, field_name)


__all__ = [
    "CalendarConsistencyResolver",
    "CalendarCoverageResolver",
    "canonical_frame_payload",
    "CompletenessRule",
    "ContractConsistencyResolver",
    "DataQualityError",
    "GapRule",
    "OrderingRule",
    "QualityEvidence",
    "QualityEvaluation",
    "QualityFinding",
    "QualityMode",
    "PublishedQualityAssessment",
    "QualityReferenceDecision",
    "QualityRequest",
    "QualityRule",
    "RangeRule",
    "RevisionBaseline",
    "RevisionRecord",
    "RevisionRule",
    "SchemaField",
    "StalenessRule",
    "UniquenessRule",
]
