"""Unit coverage for the read-only, diagnostic-only P7 Ops Agent."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.application.ops_agent import (
    OpsAgent,
    OpsAgentError,
    OpsAgentRequest,
    OpsAgentResult,
)
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
    snapshot: OpsSnapshotSummary
    fail: bool = False
    calls: list[InspectOpsSnapshotRequest] = field(default_factory=list)

    def inspect_ops_snapshot(
        self,
        *,
        request: InspectOpsSnapshotRequest,
    ) -> OpsSnapshotSummary:
        self.calls.append(request)
        if self.fail:
            raise OpsToolError("simulated ops-snapshot catalog failure")
        return self.snapshot


@dataclass
class AgentFixture:
    agent: OpsAgent
    api: TypedOpsToolApi
    request: OpsAgentRequest
    now: datetime
    snapshot: OpsSnapshotSummary
    catalog: FakeOpsSnapshotCatalog


def _snapshot(
    *,
    now: datetime,
    kill_switch_state: KillSwitchObservationState = KillSwitchObservationState.DISABLED,
    risk_state: RiskObservationState = RiskObservationState.NORMAL,
) -> OpsSnapshotSummary:
    scope_hash = _hash("ops-scope-v1")
    section_observed_at = now - timedelta(minutes=15)
    section_available_at = now - timedelta(minutes=10)
    health = HealthReportSummary(
        scope_hash=scope_hash,
        observations=tuple(
            HealthObservation(
                kind=kind,
                state=HealthObservationState.HEALTHY,
                evidence_hash=_hash(f"health-{kind.value}-evidence-v1"),
                observed_at=section_observed_at,
                available_at=section_available_at,
            )
            for kind in HealthObservationKind
        ),
        kill_switch_state=kill_switch_state,
        risk_state=risk_state,
        observed_at=section_observed_at,
        available_at=section_available_at,
        authorization_status="AUTHORIZED",
    )
    logs = LogSummary(
        scope_hash=scope_hash,
        buckets=(
            OpsLogBucket(
                severity=LogSeverity.INFO,
                code=OpsLogCode.HEALTH_CHECK,
                count=3,
                evidence_hash=_hash("ops-log-health-evidence-v1"),
                observed_at=section_observed_at,
                available_at=section_available_at,
            ),
        ),
        summary_evidence_hash=_hash("ops-log-summary-evidence-v1"),
        observed_at=section_observed_at,
        available_at=section_available_at,
        authorization_status="AUTHORIZED",
    )
    deployment = DeploymentDiagnosisSummary(
        scope_hash=scope_hash,
        state=DeploymentDiagnosisState.HEALTHY,
        evidence_hash=_hash("ops-deployment-evidence-v1"),
        observed_at=section_observed_at,
        available_at=section_available_at,
        authorization_status="AUTHORIZED",
    )
    backup = BackupStatusSummary(
        scope_hash=scope_hash,
        state=BackupStatusState.READY,
        evidence_hash=_hash("ops-backup-evidence-v1"),
        observed_at=section_observed_at,
        available_at=section_available_at,
        authorization_status="AUTHORIZED",
    )
    return OpsSnapshotSummary(
        scope_hash=scope_hash,
        snapshot_hash=_hash("ops-snapshot-v1"),
        health_report=health,
        log_summary=logs,
        deployment_diagnosis=deployment,
        backup_status=backup,
        observed_at=now - timedelta(minutes=5),
        available_at=now - timedelta(minutes=1),
        authorization_status="AUTHORIZED",
    )


def _fixture() -> AgentFixture:
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(now=now)
    catalog = FakeOpsSnapshotCatalog(snapshot=snapshot)
    api = TypedOpsToolApi(OpsToolDependencies(ops_snapshot_catalog=catalog))
    request = OpsAgentRequest(run_id="ops-agent-run-1", as_of=now)
    return AgentFixture(
        agent=OpsAgent(api),
        api=api,
        request=request,
        now=now,
        snapshot=snapshot,
        catalog=catalog,
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def test_ops_agent_returns_one_atomic_hash_only_snapshot_through_one_closed_call() -> None:
    fixture = _fixture()

    result = fixture.agent.run(fixture.request)

    assert result.lifecycle == "DIAGNOSTIC_ONLY"
    assert result.eligible_for_trading is False
    assert result.snapshot == fixture.snapshot
    assert result.snapshot.eligible_for_trading is False
    assert result.snapshot.log_summary.redaction_verified is True
    assert result.snapshot.health_report.kill_switch_state is KillSwitchObservationState.DISABLED
    assert result.snapshot.health_report.risk_state is RiskObservationState.NORMAL
    assert len(result.trace) == 1
    trace = result.trace[0]
    assert (
        trace.sequence,
        trace.tool_name,
        trace.predecessor_trace_hash,
    ) == (1, OpsToolName.INSPECT_OPS_SNAPSHOT, None)
    assert all(_is_sha256(value) for value in (trace.request_hash, trace.response_hash, trace.trace_hash))
    for raw_value in ("api_key=not-for-output", "/opt/northstar", "top-secret"):  # secret-scan: allow; reason: disposable test fixture
        assert raw_value not in repr(result)
        assert raw_value not in repr(trace)
    with pytest.raises(FrozenInstanceError):
        trace.sequence = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.trace += ()  # type: ignore[misc]
    assert fixture.catalog.calls == [InspectOpsSnapshotRequest(as_of=fixture.now)]


@pytest.mark.parametrize(
    ("kill_switch_state", "risk_state"),
    (
        (KillSwitchObservationState.ENABLED, RiskObservationState.HALT),
        (KillSwitchObservationState.UNKNOWN, RiskObservationState.UNKNOWN),
        (KillSwitchObservationState.DISABLED, RiskObservationState.MANUAL_RECOVERY),
    ),
)
def test_ops_agent_preserves_kill_switch_and_risk_states_as_observations_only(
    kill_switch_state: KillSwitchObservationState,
    risk_state: RiskObservationState,
) -> None:
    fixture = _fixture()
    fixture.catalog.snapshot = _snapshot(
        now=fixture.now,
        kill_switch_state=kill_switch_state,
        risk_state=risk_state,
    )

    result = fixture.agent.run(fixture.request)

    assert result.snapshot.health_report.kill_switch_state is kill_switch_state
    assert result.snapshot.health_report.risk_state is risk_state
    assert result.lifecycle == "DIAGNOSTIC_ONLY"
    assert result.eligible_for_trading is False
    assert len(fixture.catalog.calls) == 1


def test_ops_agent_result_has_no_control_or_raw_output_fields() -> None:
    field_names = {item.name for item in fields(OpsAgentResult)}

    assert field_names == {"run_id", "as_of", "snapshot", "trace", "lifecycle", "eligible_for_trading"}
    assert not field_names.intersection(
        {
            "action",
            "command",
            "host",
            "order",
            "path",
            "recommendation",
            "recovery",
            "secret",
            "shell",
            "target",
            "text",
            "url",
        }
    )


def test_ops_agent_fails_closed_for_a_future_atomic_snapshot() -> None:
    fixture = _fixture()
    fixture.catalog.snapshot = replace(
        fixture.snapshot,
        available_at=fixture.now + timedelta(seconds=1),
    )

    with pytest.raises(OpsToolError, match="not available"):
        fixture.agent.run(fixture.request)
    with pytest.raises(OpsAgentError, match="cannot be retried or replayed"):
        fixture.agent.run(fixture.request)

    assert len(fixture.catalog.calls) == 1


def test_ops_agent_rejects_a_malformed_snapshot_without_a_second_call() -> None:
    fixture = _fixture()
    object.__setattr__(fixture.snapshot.log_summary, "redaction_verified", False)

    with pytest.raises(OpsAgentError, match="redaction is not verified"):
        fixture.agent.run(fixture.request)
    with pytest.raises(OpsAgentError, match="cannot be retried or replayed"):
        fixture.agent.run(fixture.request)

    assert len(fixture.catalog.calls) == 1


def test_ops_agent_rejects_an_unexpected_tool_response_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()

    def unexpected_response(
        self: TypedOpsToolApi,
        tool_name: OpsToolName,
        request: object,
    ) -> object:
        assert self is fixture.api
        assert tool_name is OpsToolName.INSPECT_OPS_SNAPSHOT
        assert type(request) is InspectOpsSnapshotRequest
        return request

    monkeypatch.setattr(TypedOpsToolApi, "invoke", unexpected_response)

    with pytest.raises(OpsAgentError, match="unexpected response type"):
        fixture.agent.run(fixture.request)
    with pytest.raises(OpsAgentError, match="cannot be retried or replayed"):
        fixture.agent.run(fixture.request)

    assert fixture.catalog.calls == []


def test_failed_run_is_consumed_before_the_single_tool_call_and_cannot_be_retried() -> None:
    fixture = _fixture()
    fixture.catalog.fail = True

    with pytest.raises(OpsToolError, match="simulated ops-snapshot catalog failure"):
        fixture.agent.run(fixture.request)
    with pytest.raises(OpsAgentError, match="cannot be retried or replayed"):
        fixture.agent.run(fixture.request)

    assert len(fixture.catalog.calls) == 1


def test_ops_agent_request_accepts_only_the_opaque_run_id_and_a_timezone_aware_as_of() -> None:
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

    assert OpsAgentRequest(run_id="ops-run-2", as_of=now).as_of == now
    with pytest.raises(OpsAgentError, match="timezone-aware"):
        OpsAgentRequest(run_id="ops-run-2", as_of=now.replace(tzinfo=None))
    with pytest.raises(OpsAgentError, match="normalized opaque identifier"):
        OpsAgentRequest(run_id="ops run", as_of=now)
