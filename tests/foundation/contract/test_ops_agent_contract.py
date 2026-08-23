"""Public P7 operations-agent and operations-tool contracts.

The operations agent is a diagnostic reader, not an autonomous operator.  Its
only capability is the separate closed ``TypedOpsToolApi``; all public output
is a fixed, atomic, hash-only observation that cannot contain an action,
recovery instruction, command, or trading authority.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
import inspect
from typing import Literal, get_args, get_origin, get_type_hints

import pytest

from northstar_quant.application.ops_agent import (
    OpsAgent,
    OpsAgentError,
    OpsAgentRequest,
    OpsAgentResult,
    OpsAgentTraceEntry,
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
    OPS_TOOL_ALLOWLIST,
    OpsLogBucket,
    OpsLogCode,
    OpsSnapshotCatalog,
    OpsSnapshotSummary,
    OpsToolDependencies,
    OpsToolName,
    TypedOpsToolApi,
    RiskObservationState,
)


_HASHES = tuple(f"{value:064x}" for value in range(1, 20))
_AS_OF = datetime(2026, 8, 23, tzinfo=UTC)
_FORBIDDEN_PUBLIC_OPERATION_NAMES = frozenset(
    {
        "approve",
        "approve_production",
        "cancel_order",
        "create_execution_plan",
        "deploy",
        "disable_kill_switch",
        "enable_live_trading",
        "recovery",
        "restart",
        "restore",
        "resume",
        "resume_risk",
        "rollback",
        "submit_order",
        "trade",
        "transition",
    }
)
_FORBIDDEN_CONTROL_FIELD_FRAGMENTS = (
    "action",
    "command",
    "recommend",
    "recovery",
    "restart",
    "restore",
    "resume",
    "rollback",
    "submit_order",
    "trade",
    "transition",
)
_FORBIDDEN_RAW_FIELD_FRAGMENTS = (
    "account",
    "credential",
    "endpoint",
    "host",
    "message",
    "path",
    "payload",
    "query",
    "secret",
    "text",
    "url",
)
_SAFE_ATTESTATION_FIELD_NAMES = frozenset({"redaction_verified"})


def _field_names(dto: type[object]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(dto))


def _assert_no_control_or_raw_fields(*dtos: type[object]) -> None:
    for dto in dtos:
        violations = sorted(
            field_name
            for field_name in _field_names(dto)
            if field_name not in {"eligible_for_trading", *_SAFE_ATTESTATION_FIELD_NAMES}
            and any(
                fragment in field_name.casefold()
                for fragment in _FORBIDDEN_CONTROL_FIELD_FRAGMENTS + _FORBIDDEN_RAW_FIELD_FRAGMENTS
            )
        )
        assert not violations, f"{dto.__name__} exposes forbidden control or raw fields: {violations}"


def _snapshot() -> OpsSnapshotSummary:
    scope_hash, snapshot_hash, *evidence_hashes = _HASHES
    observations = tuple(
        HealthObservation(
            kind=kind,
            state=HealthObservationState.HEALTHY,
            evidence_hash=evidence_hashes[index],
            observed_at=_AS_OF,
            available_at=_AS_OF,
        )
        for index, kind in enumerate(HealthObservationKind)
    )
    health = HealthReportSummary(
        scope_hash=scope_hash,
        observations=observations,
        kill_switch_state=KillSwitchObservationState.DISABLED,
        risk_state=RiskObservationState.NORMAL,
        observed_at=_AS_OF,
        available_at=_AS_OF,
        authorization_status="AUTHORIZED",
    )
    logs = LogSummary(
        scope_hash=scope_hash,
        buckets=(
            OpsLogBucket(
                severity=LogSeverity.WARNING,
                code=OpsLogCode.HEALTH_CHECK,
                count=1,
                evidence_hash=evidence_hashes[4],
                observed_at=_AS_OF,
                available_at=_AS_OF,
            ),
        ),
        summary_evidence_hash=evidence_hashes[5],
        observed_at=_AS_OF,
        available_at=_AS_OF,
        authorization_status="AUTHORIZED",
    )
    deployment = DeploymentDiagnosisSummary(
        scope_hash=scope_hash,
        state=DeploymentDiagnosisState.HEALTHY,
        evidence_hash=evidence_hashes[6],
        observed_at=_AS_OF,
        available_at=_AS_OF,
        authorization_status="AUTHORIZED",
    )
    backup = BackupStatusSummary(
        scope_hash=scope_hash,
        state=BackupStatusState.READY,
        evidence_hash=evidence_hashes[7],
        observed_at=_AS_OF,
        available_at=_AS_OF,
        authorization_status="AUTHORIZED",
    )
    return OpsSnapshotSummary(
        scope_hash=scope_hash,
        snapshot_hash=snapshot_hash,
        health_report=health,
        log_summary=logs,
        deployment_diagnosis=deployment,
        backup_status=backup,
        observed_at=_AS_OF,
        available_at=_AS_OF,
        authorization_status="AUTHORIZED",
    )


def _trace() -> OpsAgentTraceEntry:
    return OpsAgentTraceEntry(
        sequence=1,
        tool_name=OpsToolName.INSPECT_OPS_SNAPSHOT,
        request_hash=_HASHES[10],
        response_hash=_HASHES[11],
        predecessor_trace_hash=None,
    )


def test_ops_tool_api_has_one_closed_read_only_operation_and_one_explicit_dependency() -> None:
    assert issubclass(OpsToolName, str)
    assert issubclass(OpsToolName, Enum)
    assert {member.value for member in OpsToolName} == {"inspect_ops_snapshot"}
    assert OPS_TOOL_ALLOWLIST == frozenset({OpsToolName.INSPECT_OPS_SNAPSHOT})

    api_signature = inspect.signature(TypedOpsToolApi)
    assert tuple(api_signature.parameters) == ("dependencies",)
    assert api_signature.parameters["dependencies"].annotation in {
        OpsToolDependencies,
        "OpsToolDependencies",
    }
    public_callables = {
        name
        for name, value in inspect.getmembers(TypedOpsToolApi, callable)
        if not name.startswith("_")
    }
    assert public_callables == {"invoke"}
    assert not public_callables.intersection(_FORBIDDEN_PUBLIC_OPERATION_NAMES)

    assert _field_names(OpsToolDependencies) == ("ops_snapshot_catalog",)
    dependency_hints = get_type_hints(OpsToolDependencies)
    assert dependency_hints["ops_snapshot_catalog"] is OpsSnapshotCatalog
    catalog_signature = inspect.signature(OpsSnapshotCatalog.inspect_ops_snapshot)
    assert tuple(catalog_signature.parameters) == ("self", "request")
    assert catalog_signature.parameters["request"].kind is inspect.Parameter.KEYWORD_ONLY
    assert catalog_signature.parameters["request"].annotation in {
        InspectOpsSnapshotRequest,
        "InspectOpsSnapshotRequest",
    }
    assert catalog_signature.return_annotation in {OpsSnapshotSummary, "OpsSnapshotSummary"}


def test_ops_agent_has_one_typed_tool_dependency_and_only_run() -> None:
    signature = inspect.signature(OpsAgent)
    parameters = tuple(signature.parameters.values())

    assert len(parameters) == 1
    assert parameters[0].annotation in {TypedOpsToolApi, "TypedOpsToolApi"}
    public_callables = {
        name
        for name, value in inspect.getmembers(OpsAgent, callable)
        if not name.startswith("_")
    }
    assert public_callables == {"run"}
    assert not public_callables.intersection(_FORBIDDEN_PUBLIC_OPERATION_NAMES)

    run_signature = inspect.signature(OpsAgent.run)
    assert tuple(run_signature.parameters) == ("self", "request")
    assert run_signature.parameters["request"].annotation in {OpsAgentRequest, "OpsAgentRequest"}
    assert run_signature.return_annotation in {OpsAgentResult, "OpsAgentResult"}


def test_ops_public_dtos_are_immutable_slotted_value_contracts() -> None:
    for dto in (
        InspectOpsSnapshotRequest,
        OpsToolDependencies,
        HealthObservation,
        HealthReportSummary,
        OpsLogBucket,
        LogSummary,
        DeploymentDiagnosisSummary,
        BackupStatusSummary,
        OpsSnapshotSummary,
        OpsAgentRequest,
        OpsAgentTraceEntry,
        OpsAgentResult,
    ):
        assert is_dataclass(dto)
        assert dto.__dataclass_params__.frozen is True
        assert hasattr(dto, "__slots__")


def test_ops_snapshot_is_exactly_one_atomic_safe_observation() -> None:
    snapshot_hints = get_type_hints(OpsSnapshotSummary)
    snapshot_fields = {field.name: field for field in fields(OpsSnapshotSummary)}

    assert _field_names(OpsSnapshotSummary) == (
        "scope_hash",
        "snapshot_hash",
        "health_report",
        "log_summary",
        "deployment_diagnosis",
        "backup_status",
        "observed_at",
        "available_at",
        "authorization_status",
        "eligible_for_trading",
    )
    assert snapshot_hints["health_report"] is HealthReportSummary
    assert snapshot_hints["log_summary"] is LogSummary
    assert snapshot_hints["deployment_diagnosis"] is DeploymentDiagnosisSummary
    assert snapshot_hints["backup_status"] is BackupStatusSummary
    assert get_origin(snapshot_hints["authorization_status"]) is Literal
    assert get_args(snapshot_hints["authorization_status"]) == ("AUTHORIZED",)
    assert get_origin(snapshot_hints["eligible_for_trading"]) is Literal
    assert get_args(snapshot_hints["eligible_for_trading"]) == (False,)
    assert snapshot_fields["eligible_for_trading"].default is False
    assert snapshot_fields["eligible_for_trading"].init is False

    _assert_no_control_or_raw_fields(
        OpsSnapshotSummary,
        HealthReportSummary,
        HealthObservation,
        LogSummary,
        OpsLogBucket,
        DeploymentDiagnosisSummary,
        BackupStatusSummary,
    )


def test_ops_snapshot_sections_are_closed_hash_and_enum_observations() -> None:
    assert _field_names(HealthObservation) == (
        "kind",
        "state",
        "evidence_hash",
        "observed_at",
        "available_at",
    )
    assert _field_names(HealthReportSummary) == (
        "scope_hash",
        "observations",
        "kill_switch_state",
        "risk_state",
        "observed_at",
        "available_at",
        "authorization_status",
        "evidence_hashes",
    )
    assert _field_names(OpsLogBucket) == (
        "severity",
        "code",
        "count",
        "evidence_hash",
        "observed_at",
        "available_at",
    )
    assert _field_names(LogSummary) == (
        "scope_hash",
        "buckets",
        "summary_evidence_hash",
        "observed_at",
        "available_at",
        "authorization_status",
        "redaction_verified",
    )
    assert _field_names(DeploymentDiagnosisSummary) == (
        "scope_hash",
        "state",
        "evidence_hash",
        "observed_at",
        "available_at",
        "authorization_status",
    )
    assert _field_names(BackupStatusSummary) == _field_names(DeploymentDiagnosisSummary)

    log_hints = get_type_hints(LogSummary)
    log_fields = {field.name: field for field in fields(LogSummary)}
    health_hints = get_type_hints(HealthReportSummary)
    health_fields = {field.name: field for field in fields(HealthReportSummary)}
    assert get_origin(health_hints["evidence_hashes"]) is tuple
    assert get_args(health_hints["evidence_hashes"]) == (str, Ellipsis)
    assert health_fields["evidence_hashes"].init is False
    assert get_origin(log_hints["redaction_verified"]) is Literal
    assert get_args(log_hints["redaction_verified"]) == (True,)
    assert log_fields["redaction_verified"].default is True
    assert log_fields["redaction_verified"].init is False
    assert {member.value for member in OpsLogCode} == {
        "health_check",
        "snapshot_collection",
        "deployment",
        "backup",
        "security_redaction",
        "unclassified",
    }


def test_ops_agent_result_is_fixed_diagnostic_only_non_tradable_and_one_step_traced() -> None:
    result_hints = get_type_hints(OpsAgentResult)
    result_fields = {field.name: field for field in fields(OpsAgentResult)}
    trace_hints = get_type_hints(OpsAgentTraceEntry)

    assert _field_names(OpsAgentResult) == (
        "run_id",
        "as_of",
        "snapshot",
        "trace",
        "lifecycle",
        "eligible_for_trading",
    )
    assert result_hints["snapshot"] is OpsSnapshotSummary
    assert get_origin(result_hints["trace"]) is tuple
    assert get_args(result_hints["trace"]) == (OpsAgentTraceEntry, Ellipsis)
    assert get_origin(result_hints["lifecycle"]) is Literal
    assert get_args(result_hints["lifecycle"]) == ("DIAGNOSTIC_ONLY",)
    assert get_origin(result_hints["eligible_for_trading"]) is Literal
    assert get_args(result_hints["eligible_for_trading"]) == (False,)
    assert result_fields["eligible_for_trading"].default is False
    assert result_fields["eligible_for_trading"].init is False
    assert trace_hints["tool_name"] is OpsToolName
    _assert_no_control_or_raw_fields(OpsAgentRequest, OpsAgentTraceEntry, OpsAgentResult)

    trace = _trace()
    result = OpsAgentResult(
        run_id="ops.run.1",
        as_of=_AS_OF,
        snapshot=_snapshot(),
        trace=(trace,),
        lifecycle="DIAGNOSTIC_ONLY",
    )
    assert result.lifecycle == "DIAGNOSTIC_ONLY"
    assert result.eligible_for_trading is False
    assert result.trace == (trace,)
    assert trace.tool_name is OpsToolName.INSPECT_OPS_SNAPSHOT

    with pytest.raises(OpsAgentError, match="one-step ops snapshot inspection"):
        OpsAgentResult(
            run_id="ops.run.2",
            as_of=_AS_OF,
            snapshot=_snapshot(),
            trace=(),
            lifecycle="DIAGNOSTIC_ONLY",
        )
