"""Fail-closed orchestration of one diagnostic-only operations snapshot.

``OpsAgent`` deliberately has no direct configuration, database, filesystem,
network, process, risk, broker, or trading capability.  It can only read one
already-sanitized immutable operations snapshot through ``TypedOpsToolApi``.
The observation may report an enabled kill switch or a halted risk state, but
it can never change either observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Literal

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
    OpsToolName,
    RiskObservationState,
    TypedOpsToolApi,
)


__all__ = [
    "OpsAgent",
    "OpsAgentError",
    "OpsAgentRequest",
    "OpsAgentResult",
    "OpsAgentTraceEntry",
]


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OpsAgentError(ValueError):
    """Raised when an operations observation cannot be safely diagnosed."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise OpsAgentError(f"{field_name} must be a string identifier")
    normalized = value.strip()
    if normalized != value or _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise OpsAgentError(f"{field_name} must be a normalized opaque identifier")
    if normalized.casefold() == "latest":
        raise OpsAgentError(f"{field_name} cannot use the ambiguous 'latest' selector")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise OpsAgentError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OpsAgentError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _fingerprint(payload: object) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class OpsAgentRequest:
    """One fixed-scope operations inspection at one explicit point in time."""

    run_id: str
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        object.__setattr__(self, "as_of", _time(self.as_of, "as_of"))


@dataclass(frozen=True, slots=True)
class OpsAgentTraceEntry:
    """A secret-free hash record for the one allowed operations read."""

    sequence: int
    tool_name: OpsToolName
    request_hash: str
    response_hash: str
    predecessor_trace_hash: str | None
    trace_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence != 1:
            raise OpsAgentError("ops trace sequence must be exactly one")
        if type(self.tool_name) is not OpsToolName or self.tool_name is not OpsToolName.INSPECT_OPS_SNAPSHOT:
            raise OpsAgentError("ops trace contains a forbidden tool")
        object.__setattr__(self, "request_hash", _sha256(self.request_hash, "request_hash"))
        object.__setattr__(self, "response_hash", _sha256(self.response_hash, "response_hash"))
        if self.predecessor_trace_hash is not None:
            raise OpsAgentError("the only ops trace entry cannot have a predecessor")
        object.__setattr__(
            self,
            "trace_hash",
            _fingerprint(
                {
                    "predecessor_trace_hash": None,
                    "request_hash": self.request_hash,
                    "response_hash": self.response_hash,
                    "sequence": self.sequence,
                    "tool_name": self.tool_name.value,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class OpsAgentResult:
    """One immutable, hash-only operations observation with no control semantics."""

    run_id: str
    as_of: datetime
    snapshot: OpsSnapshotSummary
    trace: tuple[OpsAgentTraceEntry, ...]
    lifecycle: Literal["DIAGNOSTIC_ONLY"]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        as_of = _time(self.as_of, "as_of")
        object.__setattr__(self, "as_of", as_of)
        _assert_snapshot(snapshot=self.snapshot, as_of=as_of)
        trace = tuple(self.trace)
        if (
            len(trace) != 1
            or type(trace[0]) is not OpsAgentTraceEntry
            or trace[0].sequence != 1
            or trace[0].tool_name is not OpsToolName.INSPECT_OPS_SNAPSHOT
            or trace[0].predecessor_trace_hash is not None
        ):
            raise OpsAgentError("trace must be the one-step ops snapshot inspection")
        if self.lifecycle != "DIAGNOSTIC_ONLY":
            raise OpsAgentError("ops output must remain DIAGNOSTIC_ONLY")
        object.__setattr__(self, "trace", trace)


class OpsAgent:
    """Read one safe operations snapshot without any control, recovery, or retry path."""

    __slots__ = ("_seen_request_hashes", "_seen_run_ids", "_tool_api")

    def __init__(self, tool_api: TypedOpsToolApi) -> None:
        if type(tool_api) is not TypedOpsToolApi:
            raise OpsAgentError("tool_api must be a TypedOpsToolApi")
        self._tool_api = tool_api
        self._seen_request_hashes: set[str] = set()
        self._seen_run_ids: set[str] = set()

    def run(self, request: OpsAgentRequest) -> OpsAgentResult:
        """Inspect exactly one atomic snapshot through the closed typed facade."""

        if type(request) is not OpsAgentRequest:
            raise OpsAgentError("request must be an OpsAgentRequest")
        request_hash = self._run_request_hash(request)
        if request.run_id in self._seen_run_ids or request_hash in self._seen_request_hashes:
            raise OpsAgentError("an ops-agent run cannot be retried or replayed automatically")
        # Consume identity before I/O.  A failed transport cannot prove that the
        # upstream observation was free of externally visible effects.
        self._seen_run_ids.add(request.run_id)
        self._seen_request_hashes.add(request_hash)
        inspection_request = InspectOpsSnapshotRequest(as_of=request.as_of)
        snapshot = self._tool_api.invoke(OpsToolName.INSPECT_OPS_SNAPSHOT, inspection_request)
        if type(snapshot) is not OpsSnapshotSummary:
            raise OpsAgentError("inspect_ops_snapshot returned an unexpected response type")
        _assert_snapshot(snapshot=snapshot, as_of=request.as_of)
        trace = OpsAgentTraceEntry(
            sequence=1,
            tool_name=OpsToolName.INSPECT_OPS_SNAPSHOT,
            request_hash=self._request_hash(inspection_request),
            response_hash=self._response_hash(snapshot),
            predecessor_trace_hash=None,
        )
        return OpsAgentResult(
            run_id=request.run_id,
            as_of=request.as_of,
            snapshot=snapshot,
            trace=(trace,),
            lifecycle="DIAGNOSTIC_ONLY",
        )

    @staticmethod
    def _request_hash(request: InspectOpsSnapshotRequest) -> str:
        return _fingerprint(
            {
                "as_of": request.as_of.isoformat(),
                "tool_name": OpsToolName.INSPECT_OPS_SNAPSHOT.value,
            }
        )

    @staticmethod
    def _response_hash(snapshot: OpsSnapshotSummary) -> str:
        return _fingerprint(_snapshot_payload(snapshot))

    @staticmethod
    def _run_request_hash(request: OpsAgentRequest) -> str:
        return _fingerprint(
            {
                "as_of": request.as_of.isoformat(),
                "run_id": request.run_id,
                "tool_name": OpsToolName.INSPECT_OPS_SNAPSHOT.value,
            }
        )


def _assert_snapshot(*, snapshot: object, as_of: datetime) -> OpsSnapshotSummary:
    """Defend the agent boundary even if a frozen DTO was forcibly malformed."""

    if type(snapshot) is not OpsSnapshotSummary:
        raise OpsAgentError("ops snapshot must be an OpsSnapshotSummary")
    _sha256(snapshot.scope_hash, "snapshot.scope_hash")
    _sha256(snapshot.snapshot_hash, "snapshot.snapshot_hash")
    if snapshot.authorization_status != "AUTHORIZED":
        raise OpsAgentError("ops snapshot authorization is not AUTHORIZED")
    if snapshot.eligible_for_trading is not False:
        raise OpsAgentError("ops snapshot cannot grant trading eligibility")
    observed_at = _time(snapshot.observed_at, "snapshot.observed_at")
    available_at = _time(snapshot.available_at, "snapshot.available_at")
    if observed_at > available_at:
        raise OpsAgentError("snapshot observed_at cannot be later than available_at")
    if available_at > as_of:
        raise OpsAgentError("ops snapshot is not visible at the requested as_of")

    health = snapshot.health_report
    logs = snapshot.log_summary
    deployment = snapshot.deployment_diagnosis
    backup = snapshot.backup_status
    if (
        type(health) is not HealthReportSummary
        or type(logs) is not LogSummary
        or type(deployment) is not DeploymentDiagnosisSummary
        or type(backup) is not BackupStatusSummary
    ):
        raise OpsAgentError("ops snapshot must contain exactly the four typed safe sections")
    sections = (health, logs, deployment, backup)
    if any(section.scope_hash != snapshot.scope_hash for section in sections):
        raise OpsAgentError("ops snapshot sections are not bound to the snapshot scope")
    if any(section.authorization_status != "AUTHORIZED" for section in sections):
        raise OpsAgentError("ops snapshot sections must be AUTHORIZED")
    for label, section in (
        ("health", health),
        ("logs", logs),
        ("deployment", deployment),
        ("backup", backup),
    ):
        section_observed_at = _time(section.observed_at, f"{label}.observed_at")
        section_available_at = _time(section.available_at, f"{label}.available_at")
        if section_observed_at > section_available_at:
            raise OpsAgentError(f"{label}.observed_at cannot be later than available_at")
        if section_observed_at > observed_at or section_available_at > available_at:
            raise OpsAgentError(f"{label} cannot be newer than the atomic snapshot")

    _assert_health(health=health, snapshot_available_at=available_at)
    _assert_logs(logs=logs, snapshot_available_at=available_at)
    _assert_deployment(deployment=deployment, snapshot_available_at=available_at)
    _assert_backup(backup=backup, snapshot_available_at=available_at)
    return snapshot


def _assert_health(*, health: HealthReportSummary, snapshot_available_at: datetime) -> None:
    if type(health.kill_switch_state) is not KillSwitchObservationState:
        raise OpsAgentError("health kill_switch_state has an unexpected type")
    if type(health.risk_state) is not RiskObservationState:
        raise OpsAgentError("health risk_state has an unexpected type")
    observations = tuple(health.observations)
    if (
        not all(type(item) is HealthObservation for item in observations)
        or tuple(item.kind for item in observations) != tuple(HealthObservationKind)
    ):
        raise OpsAgentError("health must contain each fixed observation exactly once")
    evidence_hashes: list[str] = []
    for observation in observations:
        if type(observation.state) is not HealthObservationState:
            raise OpsAgentError("health observation state has an unexpected type")
        evidence_hashes.append(_sha256(observation.evidence_hash, "health evidence_hash"))
        observed_at = _time(observation.observed_at, "health observation.observed_at")
        available_at = _time(observation.available_at, "health observation.available_at")
        if observed_at > available_at or available_at > health.available_at:
            raise OpsAgentError("health observation has unsafe timestamps")
        if available_at > snapshot_available_at:
            raise OpsAgentError("health observation is newer than the atomic snapshot")
    if len(set(evidence_hashes)) != len(evidence_hashes):
        raise OpsAgentError("health observations cannot duplicate evidence hashes")


def _assert_logs(*, logs: LogSummary, snapshot_available_at: datetime) -> None:
    if logs.redaction_verified is not True:
        raise OpsAgentError("log summary redaction is not verified")
    _sha256(logs.summary_evidence_hash, "log summary evidence_hash")
    buckets = tuple(logs.buckets)
    if len(buckets) > 32 or not all(type(item) is OpsLogBucket for item in buckets):
        raise OpsAgentError("log summary buckets have an unexpected type or count")
    if len({(item.severity, item.code) for item in buckets}) != len(buckets):
        raise OpsAgentError("log summary buckets cannot duplicate severity/code pairs")
    evidence_hashes = {logs.summary_evidence_hash}
    for bucket in buckets:
        if type(bucket.severity) is not LogSeverity or type(bucket.code) is not OpsLogCode:
            raise OpsAgentError("log bucket has an unexpected severity or code")
        if type(bucket.count) is not int or not 1 <= bucket.count <= 1_000_000:
            raise OpsAgentError("log bucket count is outside the safe bound")
        evidence_hash = _sha256(bucket.evidence_hash, "log bucket evidence_hash")
        if evidence_hash in evidence_hashes:
            raise OpsAgentError("log summary cannot duplicate evidence hashes")
        evidence_hashes.add(evidence_hash)
        observed_at = _time(bucket.observed_at, "log bucket.observed_at")
        available_at = _time(bucket.available_at, "log bucket.available_at")
        if observed_at > available_at or available_at > logs.available_at:
            raise OpsAgentError("log bucket has unsafe timestamps")
        if available_at > snapshot_available_at:
            raise OpsAgentError("log bucket is newer than the atomic snapshot")


def _assert_deployment(
    *,
    deployment: DeploymentDiagnosisSummary,
    snapshot_available_at: datetime,
) -> None:
    if type(deployment.state) is not DeploymentDiagnosisState:
        raise OpsAgentError("deployment state has an unexpected type")
    _sha256(deployment.evidence_hash, "deployment evidence_hash")
    if _time(deployment.available_at, "deployment.available_at") > snapshot_available_at:
        raise OpsAgentError("deployment diagnosis is newer than the atomic snapshot")


def _assert_backup(*, backup: BackupStatusSummary, snapshot_available_at: datetime) -> None:
    if type(backup.state) is not BackupStatusState:
        raise OpsAgentError("backup state has an unexpected type")
    _sha256(backup.evidence_hash, "backup evidence_hash")
    if _time(backup.available_at, "backup.available_at") > snapshot_available_at:
        raise OpsAgentError("backup status is newer than the atomic snapshot")


def _snapshot_payload(snapshot: OpsSnapshotSummary) -> dict[str, object]:
    """Return only closed states, counts, timestamps, and opaque hashes for tracing."""

    return {
        "authorization_status": snapshot.authorization_status,
        "available_at": snapshot.available_at.isoformat(),
        "backup_status": {
            "available_at": snapshot.backup_status.available_at.isoformat(),
            "evidence_hash": snapshot.backup_status.evidence_hash,
            "observed_at": snapshot.backup_status.observed_at.isoformat(),
            "state": snapshot.backup_status.state.value,
        },
        "deployment_diagnosis": {
            "available_at": snapshot.deployment_diagnosis.available_at.isoformat(),
            "evidence_hash": snapshot.deployment_diagnosis.evidence_hash,
            "observed_at": snapshot.deployment_diagnosis.observed_at.isoformat(),
            "state": snapshot.deployment_diagnosis.state.value,
        },
        "health_report": {
            "available_at": snapshot.health_report.available_at.isoformat(),
            "kill_switch_state": snapshot.health_report.kill_switch_state.value,
            "observations": [
                {
                    "available_at": observation.available_at.isoformat(),
                    "evidence_hash": observation.evidence_hash,
                    "kind": observation.kind.value,
                    "observed_at": observation.observed_at.isoformat(),
                    "state": observation.state.value,
                }
                for observation in snapshot.health_report.observations
            ],
            "observed_at": snapshot.health_report.observed_at.isoformat(),
            "risk_state": snapshot.health_report.risk_state.value,
        },
        "log_summary": {
            "available_at": snapshot.log_summary.available_at.isoformat(),
            "buckets": [
                {
                    "available_at": bucket.available_at.isoformat(),
                    "code": bucket.code.value,
                    "count": bucket.count,
                    "evidence_hash": bucket.evidence_hash,
                    "observed_at": bucket.observed_at.isoformat(),
                    "severity": bucket.severity.value,
                }
                for bucket in snapshot.log_summary.buckets
            ],
            "observed_at": snapshot.log_summary.observed_at.isoformat(),
            "redaction_verified": snapshot.log_summary.redaction_verified,
            "summary_evidence_hash": snapshot.log_summary.summary_evidence_hash,
        },
        "observed_at": snapshot.observed_at.isoformat(),
        "scope_hash": snapshot.scope_hash,
        "snapshot_hash": snapshot.snapshot_hash,
        "tool_name": OpsToolName.INSPECT_OPS_SNAPSHOT.value,
    }
