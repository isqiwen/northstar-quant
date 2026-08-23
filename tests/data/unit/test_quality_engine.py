"""P1-WP05 Data Quality Engine 的规则、PIT 和预发布门禁测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib

import polars as pl
import pytest

from northstar_quant.data.contracts.data_domain import (
    ArtifactMetadata,
    ArtifactProvenance,
    NormalizedArtifact,
    QualityStatus,
    RawArtifact,
)
from northstar_quant.data.artifacts.fingerprints import normalization_identity_hash
from northstar_quant.data.quality import (
    CompletenessRule,
    DataQualityEngine,
    DataQualityError,
    GapRule,
    OrderingRule,
    QualityEvidence,
    QualityMode,
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


UTC_TIME = datetime(2026, 1, 5, 9, tzinfo=UTC)
ACQUIRED_AT = UTC_TIME - timedelta(hours=1)
AVAILABLE_AT = UTC_TIME + timedelta(hours=1)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact(
    *,
    artifact_id: str = "candidate-v1",
    content_hash: str | None = None,
    quality_status: QualityStatus = QualityStatus.PASS,
    acquired_at: datetime = ACQUIRED_AT,
    available_at: datetime = AVAILABLE_AT,
    source_id: str = "fixture-source",
    schema_version: str = "market.fixture.v1",
    transform_version: str = "capture.fixture.v1",
) -> RawArtifact:
    return RawArtifact(
        metadata=ArtifactMetadata(
            artifact_id=artifact_id,
            source_id=source_id,
            acquired_at=acquired_at,
            available_at=available_at,
            schema_version=schema_version,
            content_hash=content_hash or _hash(artifact_id),
            transform_version=transform_version,
            quality_status=quality_status,
            provenance=ArtifactProvenance(
                source_id=source_id,
                source_reference="fixture://quality-engine",
                collection_method="fixture",
            ),
        ),
        raw_format="application/json",
    )


def _frame(*, revised: bool = False, gap: bool = False) -> pl.DataFrame:
    timestamps = [
        UTC_TIME - timedelta(minutes=45),
        UTC_TIME - timedelta(minutes=30),
        UTC_TIME - timedelta(minutes=15),
    ]
    if gap:
        timestamps[2] = UTC_TIME + timedelta(minutes=15)
    return pl.DataFrame(
        {
            "symbol": ["RB", "RB", "RB"],
            "timestamp": timestamps,
            "price": [100.0, 101.0, 103.0 if revised else 102.0],
            "volume": [10, 11, 12],
        }
    )


def _frame_hash(frame: pl.DataFrame) -> str:
    return hashlib.sha256(canonical_frame_payload(frame)).hexdigest()


def _prior_artifact(
    frame: pl.DataFrame,
    *,
    artifact_id: str = "prior-v1",
    quality_status: QualityStatus = QualityStatus.PASS,
    source_id: str = "fixture-source",
    schema_version: str = "market.fixture.v1",
    transform_version: str = "capture.fixture.v1",
) -> RawArtifact:
    return _artifact(
        artifact_id=artifact_id,
        content_hash=_frame_hash(frame),
        quality_status=quality_status,
        acquired_at=UTC_TIME - timedelta(hours=2),
        available_at=UTC_TIME - timedelta(minutes=30),
        source_id=source_id,
        schema_version=schema_version,
        transform_version=transform_version,
    )


def _normalized_prior_artifact(frame: pl.DataFrame) -> NormalizedArtifact:
    raw_parent = _artifact(
        artifact_id="normalized-parent",
        acquired_at=UTC_TIME - timedelta(hours=3),
        available_at=UTC_TIME - timedelta(hours=2),
    )
    content_hash = _frame_hash(frame)
    metadata = ArtifactMetadata(
        artifact_id="normalized-prior",
        source_id="fixture-source",
        acquired_at=UTC_TIME - timedelta(hours=1),
        available_at=UTC_TIME - timedelta(minutes=30),
        schema_version="market.fixture.v1",
        content_hash=content_hash,
        transform_version="normalize.fixture.v1",
        quality_status=QualityStatus.PASS,
        provenance=ArtifactProvenance(
            source_id="fixture-source",
            source_reference="fixture://normalized-quality-engine",
            collection_method="fixture",
        ),
    )
    return NormalizedArtifact(
        metadata=metadata,
        raw_artifact=raw_parent,
        normalization_identity=normalization_identity_hash(
            raw_parent.content_hash,
            content_hash,
            metadata.transform_version,
            metadata.schema_version,
        ),
    )


def _baseline(artifact: RawArtifact | NormalizedArtifact, frame: pl.DataFrame) -> RevisionBaseline:
    return RevisionBaseline.from_frame(
        artifact=artifact,
        frame=frame,
        key_columns=("symbol", "timestamp"),
        content_columns=("price", "volume"),
    )


class _CalendarResolver:
    def __init__(self, decision: QualityReferenceDecision) -> None:
        self._decision = decision

    def assess_calendar_consistency(self, **_: object) -> QualityReferenceDecision:
        return self._decision


class _MutatingCalendarResolver(_CalendarResolver):
    def assess_calendar_consistency(
        self,
        *,
        frame: pl.DataFrame,
        **_: object,
    ) -> QualityReferenceDecision:
        frame[0, "price"] = 999.0
        return self._decision


class _ContractResolver:
    def __init__(self, decision: QualityReferenceDecision) -> None:
        self._decision = decision

    def assess_contract_consistency(self, **_: object) -> QualityReferenceDecision:
        return self._decision


class _CoverageResolver:
    def __init__(self, decision: QualityReferenceDecision) -> None:
        self._decision = decision

    def assess_expected_observation(self, **_: object) -> QualityReferenceDecision:
        return self._decision


def _reference(
    status: QualityStatus = QualityStatus.PASS,
    *,
    available_at: datetime = UTC_TIME - timedelta(minutes=1),
    expected_observation: bool | None = None,
    name: str = "reference",
) -> QualityReferenceDecision:
    return QualityReferenceDecision(
        status=status,
        reason_code="REFERENCE_CONFIRMED" if status is QualityStatus.PASS else "REFERENCE_REJECTED",
        summary="离线 fixture 的可审计事实结论。",
        available_at=available_at,
        reference_hash=_hash(name),
        evidence=QualityEvidence.from_mapping({"fixture": name}),
        expected_observation=expected_observation,
    )


def _request(
    *,
    artifact: RawArtifact | None = None,
    frame: pl.DataFrame | None = None,
    calendar: QualityReferenceDecision | None = None,
    contract: QualityReferenceDecision | None = None,
    coverage: QualityReferenceDecision | None = None,
    baseline: RevisionBaseline | None | object = ...,
    staleness: StalenessRule | None = None,
    gap: GapRule | None = None,
    ranges: tuple[RangeRule, ...] = (RangeRule("price", 1.0, 200.0),),
    critical_rules: frozenset[QualityRule] | None = None,
) -> QualityRequest:
    data = frame if frame is not None else _frame()
    candidate = artifact or _artifact(content_hash=_frame_hash(data))
    prior_frame = _frame()
    prior = _artifact(
        artifact_id="prior-v1",
        content_hash=_frame_hash(prior_frame),
        acquired_at=UTC_TIME - timedelta(hours=2),
        available_at=UTC_TIME - timedelta(minutes=30),
    )
    resolved_baseline = (
        RevisionBaseline.from_frame(
            artifact=prior,
            frame=prior_frame,
            key_columns=("symbol", "timestamp"),
            content_columns=("price", "volume"),
        )
        if baseline is ...
        else baseline
    )
    calendar_resolver = None if calendar is None else _CalendarResolver(calendar)
    contract_resolver = None if contract is None else _ContractResolver(contract)
    coverage_resolver = None if coverage is None else _CoverageResolver(coverage)
    return QualityRequest(
        artifact=candidate,
        frame=data,
        checked_at=UTC_TIME,
        decision_at=UTC_TIME,
        completeness=CompletenessRule(("symbol", "timestamp", "price", "volume"), 3, 0.0),
        uniqueness=UniquenessRule(("symbol", "timestamp")),
        ordering=OrderingRule(("timestamp",), ("symbol",)),
        schema=tuple(
            SchemaField(name, str(data.schema[name]), False) for name in data.columns
        ),
        expected_artifact_schema_version=candidate.schema_version,
        allow_additional_columns=False,
        ranges=ranges,
        staleness=staleness or StalenessRule(timedelta(minutes=90), timedelta(hours=2)),
        gap=gap
        or GapRule(
            "timestamp",
            timedelta(minutes=30),
            ("symbol",),
            coverage_start=UTC_TIME - timedelta(hours=1),
            coverage_end=UTC_TIME + timedelta(minutes=30),
        ),
        revision=RevisionRule(
            ("symbol", "timestamp"),
            ("price", "volume"),
            QualityStatus.WARN,
            resolved_baseline if isinstance(resolved_baseline, RevisionBaseline) else None,
        ),
        policy_id="fixture-quality-policy",
        policy_version="v1",
        evaluated_payload=canonical_frame_payload(data),
        calendar_resolver=calendar_resolver,
        contract_resolver=contract_resolver,
        calendar_coverage_resolver=coverage_resolver,
        calendar_resolver_identity=None if calendar_resolver is None else "fixture-calendar-v1",
        contract_resolver_identity=None if contract_resolver is None else "fixture-contract-v1",
        calendar_coverage_resolver_identity=None if coverage_resolver is None else "fixture-coverage-v1",
        critical_rules=critical_rules or frozenset(QualityRule),
    )


def _status(evaluation, rule: QualityRule) -> QualityStatus:
    return evaluation.finding_for(rule).status


def test_engine_evaluates_all_ten_rules_and_binds_matching_published_artifact() -> None:
    request = _request(
        calendar=_reference(name="calendar"),
        contract=_reference(name="contract"),
        coverage=_reference(expected_observation=False, name="coverage"),
    )

    evaluation = DataQualityEngine().evaluate(request)

    assert {finding.rule for finding in evaluation.findings} == set(QualityRule)
    assert all(finding.status is QualityStatus.PASS for finding in evaluation.findings)
    assert evaluation.aggregate_status is QualityStatus.PASS
    assert len(evaluation.evaluation_hash) == 64
    assert len(evaluation.policy_hash) == 64
    results = evaluation.bind_published_artifact(request.artifact)
    assert len(results) == 10
    assert {result.check_id for result in results} == {
        f"quality.{rule.value}" for rule in QualityRule
    }


def test_core_rules_detect_completeness_uniqueness_ordering_schema_and_range_failures() -> None:
    engine = DataQualityEngine()
    happy = _request(
        calendar=_reference(name="calendar"),
        contract=_reference(name="contract"),
        coverage=_reference(expected_observation=False, name="coverage"),
    )

    incomplete = engine.evaluate(
        _request(
            frame=_frame().with_columns(pl.lit(None).cast(pl.Float64).alias("price")),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    duplicate = engine.evaluate(
        _request(
            frame=pl.concat([_frame(), _frame().head(1)], how="vertical"),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    unordered = engine.evaluate(
        _request(
            frame=_frame().slice(1, 2).vstack(_frame().head(1)),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    bad_schema = engine.evaluate(
        replace(happy, schema=(SchemaField("price", "Int64", False),))
    )
    out_of_range = engine.evaluate(
        _request(
            frame=_frame().with_columns(pl.lit(999.0).alias("price")),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    extra_frame = _frame().with_columns(pl.lit("extra").alias("unexpected"))
    extra_request = _request(
        frame=extra_frame,
        calendar=_reference(name="calendar"),
        contract=_reference(name="contract"),
        coverage=_reference(expected_observation=False, name="coverage"),
    )
    no_extra_schema = tuple(field for field in extra_request.schema if field.name != "unexpected")
    extra_column = engine.evaluate(replace(extra_request, schema=no_extra_schema))
    schema_version_mismatch = engine.evaluate(
        replace(happy, expected_artifact_schema_version="market.fixture.v2")
    )
    explicitly_allowed_extra = engine.evaluate(
        replace(extra_request, schema=no_extra_schema, allow_additional_columns=True)
    )

    assert _status(incomplete, QualityRule.COMPLETENESS) is QualityStatus.FAIL
    assert _status(duplicate, QualityRule.UNIQUENESS) is QualityStatus.FAIL
    assert _status(unordered, QualityRule.ORDERING) is QualityStatus.FAIL
    assert _status(bad_schema, QualityRule.SCHEMA) is QualityStatus.FAIL
    assert _status(out_of_range, QualityRule.RANGE) is QualityStatus.FAIL
    assert _status(extra_column, QualityRule.SCHEMA) is QualityStatus.FAIL
    assert _status(schema_version_mismatch, QualityRule.SCHEMA) is QualityStatus.FAIL
    assert _status(explicitly_allowed_extra, QualityRule.SCHEMA) is QualityStatus.PASS


def test_calendar_contract_staleness_gap_and_revision_rules_are_auditable() -> None:
    engine = DataQualityEngine()
    calendar_fail = engine.evaluate(
        _request(
            calendar=_reference(QualityStatus.FAIL, name="calendar-fail"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    contract_fail = engine.evaluate(
        _request(
            calendar=_reference(name="calendar"),
            contract=_reference(QualityStatus.FAIL, name="contract-fail"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    stale = engine.evaluate(
        _request(
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
            staleness=StalenessRule(None, timedelta(hours=1)),
        )
    )
    data_gap = engine.evaluate(
        _request(
            frame=_frame(gap=True),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=True, name="coverage"),
        )
    )
    revision_warn = engine.evaluate(
        _request(
            frame=_frame(revised=True),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )

    assert _status(calendar_fail, QualityRule.CALENDAR_CONSISTENCY) is QualityStatus.FAIL
    assert _status(contract_fail, QualityRule.CONTRACT_CONSISTENCY) is QualityStatus.FAIL
    assert _status(stale, QualityRule.STALENESS) is QualityStatus.FAIL
    assert _status(data_gap, QualityRule.GAP) is QualityStatus.FAIL
    assert _status(revision_warn, QualityRule.REVISION) is QualityStatus.WARN
    finding = revision_warn.finding_for(QualityRule.REVISION)
    assert finding.evidence.as_mapping()["baseline_snapshot_hash"]


def test_missing_or_future_reference_facts_and_no_revision_baseline_are_unknown() -> None:
    engine = DataQualityEngine()
    missing = engine.evaluate(_request(baseline=None))
    future = engine.evaluate(
        _request(
            calendar=_reference(available_at=UTC_TIME + timedelta(minutes=1), name="future-calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )

    assert _status(missing, QualityRule.CALENDAR_CONSISTENCY) is QualityStatus.UNKNOWN
    assert _status(missing, QualityRule.CONTRACT_CONSISTENCY) is QualityStatus.UNKNOWN
    assert _status(missing, QualityRule.GAP) is QualityStatus.UNKNOWN
    assert _status(missing, QualityRule.REVISION) is QualityStatus.UNKNOWN
    assert _status(future, QualityRule.CALENDAR_CONSISTENCY) is QualityStatus.UNKNOWN
    assert future.finding_for(QualityRule.CALENDAR_CONSISTENCY).reason_code == "REFERENCE_NOT_VISIBLE_AT_PIT"


def test_revision_rejects_untrusted_or_incompatible_prior_baselines() -> None:
    engine = DataQualityEngine()
    prior_frame = _frame()
    baselines = (
        _baseline(_prior_artifact(prior_frame, quality_status=QualityStatus.FAIL), prior_frame),
        _baseline(_prior_artifact(prior_frame, quality_status=QualityStatus.UNKNOWN), prior_frame),
        _baseline(_prior_artifact(prior_frame, source_id="other-source"), prior_frame),
        _baseline(_prior_artifact(prior_frame, schema_version="market.fixture.v2"), prior_frame),
        _baseline(_prior_artifact(prior_frame, transform_version="capture.fixture.v2"), prior_frame),
        _baseline(_normalized_prior_artifact(prior_frame), prior_frame),
    )

    for baseline in baselines:
        evaluation = engine.evaluate(
            _request(
                baseline=baseline,
                calendar=_reference(name="calendar"),
                contract=_reference(name="contract"),
                coverage=_reference(expected_observation=False, name="coverage"),
            )
        )
        assert _status(evaluation, QualityRule.REVISION) is QualityStatus.UNKNOWN


def test_revision_removed_or_nonoverlapping_prior_keys_never_silently_pass() -> None:
    engine = DataQualityEngine()
    removed = engine.evaluate(
        _request(
            frame=_frame().head(2),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    no_overlap = engine.evaluate(
        _request(
            frame=_frame().with_columns(pl.lit("CU").alias("symbol")),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )

    removed_finding = removed.finding_for(QualityRule.REVISION)
    no_overlap_finding = no_overlap.finding_for(QualityRule.REVISION)
    assert removed_finding.status is QualityStatus.WARN
    assert removed_finding.evidence.as_mapping()["removed_key_count"] == 1
    assert no_overlap_finding.status is QualityStatus.UNKNOWN
    assert no_overlap_finding.evidence.as_mapping() == {
        "added_key_count": 3,
        "baseline_snapshot_hash": no_overlap_finding.evidence.as_mapping()["baseline_snapshot_hash"],
        "removed_key_count": 3,
    }


def test_gap_never_concludes_without_visible_calendar_coverage() -> None:
    engine = DataQualityEngine()
    unknown = engine.evaluate(
        _request(
            frame=_frame(gap=True),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
        )
    )
    closed_interval = engine.evaluate(
        _request(
            frame=_frame(gap=True),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="closed-interval"),
        )
    )
    unknown_coverage = engine.evaluate(
        _request(
            frame=_frame(gap=True),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(
                QualityStatus.UNKNOWN,
                expected_observation=False,
                name="unknown-coverage",
            ),
        )
    )

    assert _status(unknown, QualityRule.GAP) is QualityStatus.UNKNOWN
    assert _status(closed_interval, QualityRule.GAP) is QualityStatus.PASS
    assert _status(unknown_coverage, QualityRule.GAP) is QualityStatus.UNKNOWN


def test_gap_requires_explicit_boundaries_and_checks_group_and_window_edges() -> None:
    engine = DataQualityEngine()
    no_boundaries = engine.evaluate(
        _request(
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
            gap=GapRule("timestamp", timedelta(minutes=30), ("symbol",)),
        )
    )
    trailing_gap = engine.evaluate(
        _request(
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=True, name="coverage"),
        )
    )
    leading_gap = engine.evaluate(
        _request(
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=True, name="coverage"),
            gap=GapRule(
                "timestamp",
                timedelta(minutes=30),
                ("symbol",),
                coverage_start=UTC_TIME - timedelta(hours=2),
                coverage_end=UTC_TIME,
            ),
        )
    )
    split_groups = engine.evaluate(
        _request(
            frame=pl.DataFrame(
                {
                    "symbol": ["RB", "CU"],
                    "timestamp": [UTC_TIME - timedelta(minutes=45), UTC_TIME - timedelta(minutes=30)],
                    "price": [100.0, 101.0],
                    "volume": [10, 11],
                }
            ),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )

    assert _status(no_boundaries, QualityRule.GAP) is QualityStatus.UNKNOWN
    assert _status(trailing_gap, QualityRule.GAP) is QualityStatus.FAIL
    assert _status(leading_gap, QualityRule.GAP) is QualityStatus.FAIL
    assert _status(split_groups, QualityRule.GAP) is QualityStatus.UNKNOWN


def test_production_gate_blocks_fail_and_critical_unknown_and_requires_explicit_warn_policy() -> None:
    engine = DataQualityEngine()
    unknown = engine.evaluate(_request(baseline=None))
    with pytest.raises(DataQualityError, match="关键 UNKNOWN"):
        unknown.require_eligible(
            mode=QualityMode.PRODUCTION,
            allow_warn=True,
            allow_unknown_for_noncritical=True,
        )

    warning = engine.evaluate(
        _request(
            frame=_frame(revised=True),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
            critical_rules=frozenset(QualityRule) - {QualityRule.REVISION},
        )
    )
    with pytest.raises(DataQualityError, match="未授权 WARN"):
        warning.require_eligible(
            mode="research",
            allow_warn=False,
            allow_unknown_for_noncritical=False,
        )
    warning.require_eligible(
        mode=QualityMode.RESEARCH,
        allow_warn=True,
        allow_unknown_for_noncritical=False,
    )
    metadata_warning = engine.evaluate(
        _request(
            artifact=_artifact(
                quality_status=QualityStatus.WARN,
                content_hash=_frame_hash(_frame()),
            ),
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    with pytest.raises(DataQualityError, match="artifact metadata=WARN"):
        metadata_warning.require_eligible(
            mode=QualityMode.PRODUCTION,
            allow_warn=False,
            allow_unknown_for_noncritical=False,
        )

    failed = engine.evaluate(
        _request(
            calendar=_reference(QualityStatus.FAIL, name="calendar-fail"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )
    with pytest.raises(DataQualityError, match="FAIL"):
        failed.require_eligible(
            mode=QualityMode.PRODUCTION,
            allow_warn=True,
            allow_unknown_for_noncritical=True,
        )


def test_failed_candidate_assessment_cannot_bind_to_metadata_pass_but_can_bind_rebuilt_status() -> None:
    failing_frame = _frame().with_columns(pl.lit(999.0).alias("price"))
    candidate = _artifact(content_hash=_frame_hash(failing_frame))
    request = _request(
        artifact=candidate,
        frame=failing_frame,
        calendar=_reference(name="calendar"),
        contract=_reference(name="contract"),
        coverage=_reference(expected_observation=False, name="coverage"),
    )
    evaluation = DataQualityEngine().evaluate(request)

    assert evaluation.aggregate_status is QualityStatus.FAIL
    with pytest.raises(DataQualityError, match="quality_status"):
        evaluation.bind_published_artifact(candidate)

    published = RawArtifact(
        metadata=replace(candidate.metadata, quality_status=QualityStatus.FAIL),
        raw_format=candidate.raw_format,
    )
    results = evaluation.bind_published_artifact(published)
    assert all(result.quality_status is not QualityStatus.PASS for result in results if result.check_id == "quality.range")


@pytest.mark.parametrize("candidate_status", [QualityStatus.FAIL, QualityStatus.UNKNOWN])
def test_bind_cannot_improve_failed_or_unknown_candidate_metadata(
    candidate_status: QualityStatus,
) -> None:
    """全 PASS finding 也不能覆盖候选制品已有的更严重 metadata 质量。"""

    frame = _frame()
    candidate = _artifact(content_hash=_frame_hash(frame), quality_status=candidate_status)
    evaluation = DataQualityEngine().evaluate(
        _request(
            artifact=candidate,
            frame=frame,
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    )

    assert evaluation.aggregate_status is QualityStatus.PASS
    forged_published = RawArtifact(
        metadata=replace(candidate.metadata, quality_status=QualityStatus.PASS),
        raw_format=candidate.raw_format,
    )
    with pytest.raises(DataQualityError, match="不得优于候选"):
        evaluation.bind_published_artifact(forged_published)


def test_quality_evidence_reference_identity_and_policy_hash_are_strict_and_stable() -> None:
    with pytest.raises(DataQualityError, match="canonical JSON"):
        QualityEvidence('{"z":1,"a":2}')
    with pytest.raises(DataQualityError, match="凭据"):
        QualityEvidence.from_mapping({"token": "plain-secret"})
    with pytest.raises(DataQualityError, match="reference_hash"):
        QualityReferenceDecision(
            status=QualityStatus.PASS,
            reason_code="REFERENCE_CONFIRMED",
            summary="无哈希的通过结论。",
            available_at=UTC_TIME,
            reference_hash=None,
        )
    with pytest.raises(DataQualityError, match="PASS、FAIL 或 UNKNOWN"):
        QualityReferenceDecision(
            status=QualityStatus.WARN,
            reason_code="REFERENCE_WARNING",
            summary="不允许的 reference 状态。",
            available_at=UTC_TIME,
            reference_hash=_hash("warn-reference"),
        )

    base = _request(
        calendar=_reference(name="calendar"),
        contract=_reference(name="contract"),
        coverage=_reference(expected_observation=False, name="coverage"),
    )
    changed_policy = replace(base, policy_version="v2")
    changed_threshold = replace(base, staleness=StalenessRule(timedelta(minutes=30), timedelta(hours=2)))

    assert base.policy_hash != changed_policy.policy_hash
    assert base.policy_hash != changed_threshold.policy_hash
    assert DataQualityEngine().evaluate(base).evaluation_hash != DataQualityEngine().evaluate(changed_policy).evaluation_hash


def test_request_rejects_pit_after_artifact_publication_and_resolver_without_stable_identity() -> None:
    with pytest.raises(DataQualityError, match="不能晚于 artifact.available_at"):
        replace(
            _request(
                calendar=_reference(name="calendar"),
                contract=_reference(name="contract"),
                coverage=_reference(expected_observation=False, name="coverage"),
            ),
            checked_at=AVAILABLE_AT + timedelta(seconds=1),
        )
    with pytest.raises(DataQualityError, match="不能晚于 artifact.available_at"):
        replace(
            _request(
                calendar=_reference(name="calendar"),
                contract=_reference(name="contract"),
                coverage=_reference(expected_observation=False, name="coverage"),
            ),
            decision_at=AVAILABLE_AT + timedelta(seconds=1),
        )
    with pytest.raises(DataQualityError, match="resolver identity"):
        replace(
            _request(
                calendar=_reference(name="calendar"),
                contract=_reference(name="contract"),
                coverage=_reference(expected_observation=False, name="coverage"),
            ),
            calendar_resolver_identity=None,
        )


def test_engine_rejects_frame_mutation_after_request_construction() -> None:
    request = _request(
        calendar=_reference(name="calendar"),
        contract=_reference(name="contract"),
        coverage=_reference(expected_observation=False, name="coverage"),
    )
    request.frame[0, "price"] = 999.0

    with pytest.raises(DataQualityError, match="发生变化"):
        DataQualityEngine().evaluate(request)


def test_resolver_receives_isolated_frame_and_cannot_mutate_later_rules() -> None:
    request = _request(
        calendar=_reference(name="calendar"),
        contract=_reference(name="contract"),
        coverage=_reference(expected_observation=False, name="coverage"),
    )
    isolated = replace(
        request,
        calendar_resolver=_MutatingCalendarResolver(_reference(name="mutating-calendar")),
        calendar_resolver_identity="mutating-calendar-v1",
    )

    evaluation = DataQualityEngine().evaluate(isolated)

    assert _status(evaluation, QualityRule.RANGE) is QualityStatus.PASS
    assert _status(evaluation, QualityRule.REVISION) is QualityStatus.PASS


def test_canonical_payload_and_artifact_hash_binding_reject_forgery() -> None:
    request = _request(
        calendar=_reference(name="calendar"),
        contract=_reference(name="contract"),
        coverage=_reference(expected_observation=False, name="coverage"),
    )
    with pytest.raises(DataQualityError, match="逐字节一致"):
        replace(request, evaluated_payload=b'{"forged":true}')

    frame = _frame()
    mismatched_artifact = _artifact(content_hash=_hash("different-payload"))
    with pytest.raises(DataQualityError, match="精确匹配 artifact.content_hash"):
        _request(
            artifact=mismatched_artifact,
            frame=frame,
            calendar=_reference(name="calendar"),
            contract=_reference(name="contract"),
            coverage=_reference(expected_observation=False, name="coverage"),
        )
    with pytest.raises(DataQualityError, match="revision_baseline.frame"):
        RevisionBaseline.from_frame(
            artifact=mismatched_artifact,
            frame=frame,
            key_columns=("symbol", "timestamp"),
            content_columns=("price", "volume"),
        )
