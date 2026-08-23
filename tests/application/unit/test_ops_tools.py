"""Unit coverage for the fail-closed P7 typed operations-tool facade."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.application.ops_tools import (
    BackupStatusState,
    BackupStatusSummary,
    DeploymentDiagnosisState,
    DeploymentDiagnosisSummary,
    HealthObservation,
    HealthObservationKind,
    HealthObservationState,
    HealthReportSummary,
    InspectOpsSnapshotRequest,
    KillSwitchObservationState,
    LogSeverity,
    LogSummary,
    OPS_TOOL_ALLOWLIST,
    OpsLogBucket,
    OpsLogCode,
    OpsSnapshotSummary,
    OpsToolDependencies,
    OpsToolError,
    OpsToolName,
    RiskObservationState,
    TypedOpsToolApi,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass
class FakeOpsSnapshotCatalog:
    result: object
    calls: list[InspectOpsSnapshotRequest] = field(default_factory=list)

    def inspect_ops_snapshot(self, *, request: InspectOpsSnapshotRequest) -> OpsSnapshotSummary:
        self.calls.append(request)
        return self.result  # type: ignore[return-value]


@dataclass
class OpsFixture:
    api: TypedOpsToolApi
    catalog: FakeOpsSnapshotCatalog
    request: InspectOpsSnapshotRequest
    snapshot: OpsSnapshotSummary
    now: datetime


def _health_report(*, scope_hash: str, now: datetime) -> HealthReportSummary:
    observed_at = now - timedelta(minutes=10)
    available_at = now - timedelta(minutes=9)
    observations = tuple(
        HealthObservation(
            kind=kind,
            state=HealthObservationState.HEALTHY,
            evidence_hash=_hash(f"health-{kind.value}"),
            observed_at=observed_at,
            available_at=available_at,
        )
        for kind in HealthObservationKind
    )
    return HealthReportSummary(
        scope_hash=scope_hash,
        observations=observations,
        kill_switch_state=KillSwitchObservationState.ENABLED,
        risk_state=RiskObservationState.HALT,
        observed_at=observed_at,
        available_at=available_at,
        authorization_status="AUTHORIZED",
    )


def _log_summary(*, scope_hash: str, now: datetime) -> LogSummary:
    observed_at = now - timedelta(minutes=8)
    available_at = now - timedelta(minutes=7)
    return LogSummary(
        scope_hash=scope_hash,
        buckets=(
            OpsLogBucket(
                severity=LogSeverity.ERROR,
                code=OpsLogCode.HEALTH_CHECK,
                count=3,
                evidence_hash=_hash("log-health-check"),
                observed_at=observed_at,
                available_at=available_at,
            ),
            OpsLogBucket(
                severity=LogSeverity.WARNING,
                code=OpsLogCode.BACKUP,
                count=1,
                evidence_hash=_hash("log-backup"),
                observed_at=observed_at,
                available_at=available_at,
            ),
        ),
        summary_evidence_hash=_hash("log-summary"),
        observed_at=observed_at,
        available_at=available_at,
        authorization_status="AUTHORIZED",
    )


def _deployment_diagnosis(*, scope_hash: str, now: datetime) -> DeploymentDiagnosisSummary:
    observed_at = now - timedelta(minutes=6)
    available_at = now - timedelta(minutes=5)
    return DeploymentDiagnosisSummary(
        scope_hash=scope_hash,
        state=DeploymentDiagnosisState.DEGRADED,
        evidence_hash=_hash("deployment"),
        observed_at=observed_at,
        available_at=available_at,
        authorization_status="AUTHORIZED",
    )


def _backup_status(*, scope_hash: str, now: datetime) -> BackupStatusSummary:
    observed_at = now - timedelta(minutes=4)
    available_at = now - timedelta(minutes=3)
    return BackupStatusSummary(
        scope_hash=scope_hash,
        state=BackupStatusState.READY,
        evidence_hash=_hash("backup"),
        observed_at=observed_at,
        available_at=available_at,
        authorization_status="AUTHORIZED",
    )


def _snapshot(*, now: datetime, scope_hash: str | None = None) -> OpsSnapshotSummary:
    scope = scope_hash or _hash("authorized-ops-scope")
    observed_at = now - timedelta(minutes=2)
    available_at = now - timedelta(minutes=1)
    return OpsSnapshotSummary(
        scope_hash=scope,
        snapshot_hash=_hash("ops-snapshot"),
        health_report=_health_report(scope_hash=scope, now=now),
        log_summary=_log_summary(scope_hash=scope, now=now),
        deployment_diagnosis=_deployment_diagnosis(scope_hash=scope, now=now),
        backup_status=_backup_status(scope_hash=scope, now=now),
        observed_at=observed_at,
        available_at=available_at,
        authorization_status="AUTHORIZED",
    )


def _fixture() -> OpsFixture:
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(now=now)
    catalog = FakeOpsSnapshotCatalog(result=snapshot)
    return OpsFixture(
        api=TypedOpsToolApi(OpsToolDependencies(ops_snapshot_catalog=catalog)),
        catalog=catalog,
        request=InspectOpsSnapshotRequest(as_of=now),
        snapshot=snapshot,
        now=now,
    )


def test_ops_tool_api_reads_one_authorized_atomic_snapshot_through_its_only_port() -> None:
    fixture = _fixture()

    result = fixture.api.invoke(OpsToolName.INSPECT_OPS_SNAPSHOT, fixture.request)

    assert result is fixture.snapshot
    assert fixture.catalog.calls == [fixture.request]
    assert result.eligible_for_trading is False
    assert result.log_summary.redaction_verified is True
    assert result.health_report.risk_state is RiskObservationState.HALT
    assert result.health_report.kill_switch_state is KillSwitchObservationState.ENABLED


def test_ops_tool_allowlist_and_request_are_closed_and_selector_free() -> None:
    assert OPS_TOOL_ALLOWLIST == frozenset({OpsToolName.INSPECT_OPS_SNAPSHOT})
    assert tuple(OpsToolName) == (OpsToolName.INSPECT_OPS_SNAPSHOT,)
    assert tuple(field.name for field in fields(InspectOpsSnapshotRequest)) == ("as_of",)


def test_snapshot_contains_exact_fixed_health_observations_and_safe_read_only_states() -> None:
    snapshot = _fixture().snapshot

    assert tuple(item.kind for item in snapshot.health_report.observations) == tuple(
        HealthObservationKind
    )
    assert snapshot.health_report.kill_switch_state is KillSwitchObservationState.ENABLED
    assert snapshot.health_report.risk_state is RiskObservationState.HALT
    assert fields(snapshot.log_summary)[-1].name == "redaction_verified"
    assert fields(snapshot.log_summary)[-1].default is True
    assert fields(snapshot.log_summary)[-1].init is False
    assert {field.name for field in fields(OpsLogBucket)} == {
        "severity",
        "code",
        "count",
        "evidence_hash",
        "observed_at",
        "available_at",
    }


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda now: InspectOpsSnapshotRequest(as_of=now.replace(tzinfo=None)),
            "timezone-aware",
        ),
        (
            lambda now: HealthObservation(
                kind="service",  # type: ignore[arg-type]
                state=HealthObservationState.HEALTHY,
                evidence_hash=_hash("health"),
                observed_at=now,
                available_at=now,
            ),
            "HealthObservationKind",
        ),
        (
            lambda now: OpsLogBucket(
                severity=LogSeverity.INFO,
                code="raw-log-message",  # type: ignore[arg-type]
                count=1,
                evidence_hash=_hash("log"),
                observed_at=now,
                available_at=now,
            ),
            "OpsLogCode",
        ),
        (
            lambda now: DeploymentDiagnosisSummary(
                scope_hash="/opt/northstar",  # type: ignore[arg-type]
                state=DeploymentDiagnosisState.HEALTHY,
                evidence_hash=_hash("deployment"),
                observed_at=now,
                available_at=now,
                authorization_status="AUTHORIZED",
            ),
            "SHA-256",
        ),
        (
            lambda now: BackupStatusSummary(
                scope_hash=_hash("scope"),
                state=BackupStatusState.READY,
                evidence_hash="token=not-for-output",  # type: ignore[arg-type]  # secret-scan: allow; reason: disposable test fixture
                observed_at=now,
                available_at=now,
                authorization_status="AUTHORIZED",
            ),
            "SHA-256",
        ),
    ],
)
def test_ops_dtos_reject_bad_types_identifiers_secrets_paths_and_naive_times(factory, match, now):
    with pytest.raises(OpsToolError, match=match):
        factory(now)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def test_health_report_rejects_missing_out_of_order_duplicate_or_future_evidence(now: datetime) -> None:
    report = _health_report(scope_hash=_hash("scope"), now=now)
    reversed_observations = tuple(reversed(report.observations))
    with pytest.raises(OpsToolError, match="exactly cover"):
        replace(report, observations=reversed_observations)

    duplicate_evidence = replace(
        report.observations[1],
        evidence_hash=report.observations[0].evidence_hash,
    )
    with pytest.raises(OpsToolError, match="duplicate evidence"):
        replace(report, observations=(report.observations[0], duplicate_evidence, *report.observations[2:]))

    future_observation = replace(
        report.observations[0],
        observed_at=report.observed_at + timedelta(minutes=1),
        available_at=report.available_at + timedelta(minutes=1),
    )
    with pytest.raises(OpsToolError, match="later than the health report"):
        replace(report, observations=(future_observation, *report.observations[1:]))


def test_log_summary_is_bounded_redacted_and_rejects_duplicate_or_unordered_buckets(now: datetime) -> None:
    summary = _log_summary(scope_hash=_hash("scope"), now=now)
    with pytest.raises(OpsToolError, match="duplicate severity/code"):
        replace(summary, buckets=(summary.buckets[0], summary.buckets[0]))
    with pytest.raises(OpsToolError, match="summary evidence"):
        replace(summary, summary_evidence_hash=summary.buckets[0].evidence_hash)
    with pytest.raises(OpsToolError, match="at most 32"):
        replace(summary, buckets=(summary.buckets[0],) * 33)
    with pytest.raises(OpsToolError, match="between 1 and"):
        replace(summary.buckets[0], count=True)
    with pytest.raises(OpsToolError, match="later than the log summary"):
        replace(
            summary,
            buckets=(
                replace(
                    summary.buckets[0],
                    observed_at=summary.observed_at + timedelta(minutes=1),
                    available_at=summary.available_at + timedelta(minutes=1),
                ),
            ),
        )


@pytest.mark.parametrize(
    ("section_name", "replacement", "match"),
    [
        (
            "deployment_diagnosis",
            lambda snapshot: replace(snapshot.deployment_diagnosis, scope_hash=_hash("other-scope")),
            "exact snapshot scope_hash",
        ),
        (
            "backup_status",
            lambda snapshot: replace(
                snapshot.backup_status,
                evidence_hash=snapshot.deployment_diagnosis.evidence_hash,
            ),
            "duplicate evidence",
        ),
        (
            "deployment_diagnosis",
            lambda snapshot: replace(
                snapshot.deployment_diagnosis,
                observed_at=snapshot.observed_at + timedelta(minutes=1),
                available_at=snapshot.available_at + timedelta(minutes=1),
            ),
            "later than the ops snapshot",
        ),
    ],
)
def test_snapshot_rejects_scope_mismatch_duplicate_or_future_section_evidence(
    section_name,
    replacement,
    match,
) -> None:
    snapshot = _fixture().snapshot
    with pytest.raises(OpsToolError, match=match):
        replace(snapshot, **{section_name: replacement(snapshot)})


@pytest.mark.parametrize(
    ("target", "match"),
    [
        ("health", "authorization_status"),
        ("logs", "authorization_status"),
        ("deployment", "authorization_status"),
        ("backup", "authorization_status"),
        ("snapshot", "authorization_status"),
    ],
)
def test_sections_and_snapshot_fail_closed_when_not_authorized(target: str, match: str) -> None:
    snapshot = _fixture().snapshot
    with pytest.raises(OpsToolError, match="AUTHORIZED"):
        if target == "health":
            replace(snapshot.health_report, authorization_status="UNAUTHORIZED")
        elif target == "logs":
            replace(snapshot.log_summary, authorization_status="UNAUTHORIZED")
        elif target == "deployment":
            replace(snapshot.deployment_diagnosis, authorization_status="UNAUTHORIZED")
        elif target == "backup":
            replace(snapshot.backup_status, authorization_status="UNAUTHORIZED")
        else:
            replace(snapshot, authorization_status="UNAUTHORIZED")


def test_typed_api_rejects_bad_dependencies_request_tool_response_and_future_snapshot() -> None:
    fixture = _fixture()
    with pytest.raises(OpsToolError, match="missing inspect_ops_snapshot"):
        OpsToolDependencies(ops_snapshot_catalog=object())  # type: ignore[arg-type]
    with pytest.raises(OpsToolError, match="OpsToolDependencies"):
        TypedOpsToolApi(object())  # type: ignore[arg-type]
    with pytest.raises(OpsToolError, match="OpsToolName"):
        fixture.api.invoke("inspect_ops_snapshot", fixture.request)  # type: ignore[arg-type]
    with pytest.raises(OpsToolError, match="InspectOpsSnapshotRequest"):
        fixture.api.invoke(OpsToolName.INSPECT_OPS_SNAPSHOT, object())  # type: ignore[arg-type]

    wrong_result_api = TypedOpsToolApi(OpsToolDependencies(ops_snapshot_catalog=FakeOpsSnapshotCatalog(None)))
    with pytest.raises(OpsToolError, match="OpsSnapshotSummary"):
        wrong_result_api.invoke(OpsToolName.INSPECT_OPS_SNAPSHOT, fixture.request)

    future_snapshot = replace(fixture.snapshot, available_at=fixture.now + timedelta(seconds=1))
    future_api = TypedOpsToolApi(
        OpsToolDependencies(ops_snapshot_catalog=FakeOpsSnapshotCatalog(future_snapshot))
    )
    with pytest.raises(OpsToolError, match="not available"):
        future_api.invoke(OpsToolName.INSPECT_OPS_SNAPSHOT, fixture.request)
