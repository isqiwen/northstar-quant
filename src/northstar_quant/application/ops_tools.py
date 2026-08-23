"""Fail-closed typed boundary for AI-assisted operational diagnosis.

This module deliberately does not extend the research-only ``agent_tools``
surface.  It exposes one atomic, read-only inspection of an already-authorized
immutable operations snapshot.  The caller must inject a trusted catalog that
has already summarized health, logs, deployment, and backup evidence without
granting this facade configuration, database, filesystem, network, shell, or
trading capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import re
from typing import Literal, Protocol, TypeAlias, cast


__all__ = [
    "BackupStatusState",
    "BackupStatusSummary",
    "DeploymentDiagnosisState",
    "DeploymentDiagnosisSummary",
    "HealthObservation",
    "HealthObservationKind",
    "HealthObservationState",
    "HealthReportSummary",
    "InspectOpsSnapshotRequest",
    "KillSwitchObservationState",
    "LogSeverity",
    "LogSummary",
    "OPS_TOOL_ALLOWLIST",
    "OpsLogBucket",
    "OpsLogCode",
    "OpsSnapshotCatalog",
    "OpsSnapshotSummary",
    "OpsToolDependencies",
    "OpsToolError",
    "OpsToolName",
    "OpsToolRequest",
    "OpsToolResponse",
    "RiskObservationState",
    "TypedOpsToolApi",
]


class OpsToolError(ValueError):
    """Raised when an operations observation is incomplete, unsafe, or ambiguous."""


class OpsToolName(StrEnum):
    """The complete, read-only operational capability allowlist for an AI caller."""

    INSPECT_OPS_SNAPSHOT = "inspect_ops_snapshot"


OPS_TOOL_ALLOWLIST: frozenset[OpsToolName] = frozenset(OpsToolName)


class HealthObservationKind(StrEnum):
    """The fixed, non-sensitive health components exposed to operations diagnostics."""

    SERVICE = "service"
    DATABASE = "database"
    MARKET_DATA = "market_data"
    BROKER = "broker"


class HealthObservationState(StrEnum):
    """Fail-closed state vocabulary for a health component."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class KillSwitchObservationState(StrEnum):
    """A read-only observation of the kill-switch state, never a control operation."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class RiskObservationState(StrEnum):
    """A read-only risk state; HALT and recovery can never be changed by this API."""

    NORMAL = "normal"
    LIMIT_ONLY = "limit_only"
    REDUCE_ONLY = "reduce_only"
    HALT = "halt"
    MANUAL_RECOVERY = "manual_recovery"
    UNKNOWN = "unknown"


class LogSeverity(StrEnum):
    """Closed severity buckets for redacted operational log summaries."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class OpsLogCode(StrEnum):
    """Closed, text-free log categories allowed in an operational snapshot."""

    HEALTH_CHECK = "health_check"
    SNAPSHOT_COLLECTION = "snapshot_collection"
    DEPLOYMENT = "deployment"
    BACKUP = "backup"
    SECURITY_REDACTION = "security_redaction"
    UNCLASSIFIED = "unclassified"


class DeploymentDiagnosisState(StrEnum):
    """Closed deployment diagnosis state with no host, process, or release detail."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class BackupStatusState(StrEnum):
    """Closed backup-readiness state with no path, artifact, or restore payload."""

    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_LOG_BUCKETS = 32
_MAX_LOG_BUCKET_COUNT = 1_000_000


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise OpsToolError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _hashes(value: object, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if isinstance(value, str):
        raise OpsToolError(f"{field_name} must be an iterable of hashes")
    try:
        hashes: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise OpsToolError(f"{field_name} must be an iterable of hashes") from exc
    if len(hashes) < minimum:
        raise OpsToolError(f"{field_name} must contain at least {minimum} hash(es)")
    normalized = tuple(_sha256(item, field_name) for item in hashes)
    if len(set(normalized)) != len(normalized):
        raise OpsToolError(f"{field_name} cannot contain duplicate evidence hashes")
    return normalized


def _records(value: object, field_name: str) -> tuple[object, ...]:
    if isinstance(value, str):
        raise OpsToolError(f"{field_name} must be an iterable of typed records")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise OpsToolError(f"{field_name} must be an iterable of typed records") from exc


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OpsToolError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _ordered_times(
    observed_at: object,
    available_at: object,
    *,
    label: str,
) -> tuple[datetime, datetime]:
    normalized_observed_at = _time(observed_at, f"{label}.observed_at")
    normalized_available_at = _time(available_at, f"{label}.available_at")
    if normalized_observed_at > normalized_available_at:
        raise OpsToolError(f"{label}.observed_at cannot be later than {label}.available_at")
    return normalized_observed_at, normalized_available_at


def _authorized(value: object, field_name: str = "authorization_status") -> Literal["AUTHORIZED"]:
    if value != "AUTHORIZED":
        raise OpsToolError(f"{field_name} must be AUTHORIZED")
    return "AUTHORIZED"


@dataclass(frozen=True, slots=True)
class HealthObservation:
    """One fixed health state with only a timestamped evidence hash."""

    kind: HealthObservationKind
    state: HealthObservationState
    evidence_hash: str
    observed_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        if type(self.kind) is not HealthObservationKind:
            raise OpsToolError("health observation kind must be a HealthObservationKind")
        if type(self.state) is not HealthObservationState:
            raise OpsToolError("health observation state must be a HealthObservationState")
        object.__setattr__(self, "evidence_hash", _sha256(self.evidence_hash, "evidence_hash"))
        observed_at, available_at = _ordered_times(
            self.observed_at,
            self.available_at,
            label="health observation",
        )
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class HealthReportSummary:
    """A fixed, hash-only report of safe health and read-only risk observations."""

    scope_hash: str
    observations: tuple[HealthObservation, ...]
    kill_switch_state: KillSwitchObservationState
    risk_state: RiskObservationState
    observed_at: datetime
    available_at: datetime
    authorization_status: Literal["AUTHORIZED"]
    evidence_hashes: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_hash", _sha256(self.scope_hash, "scope_hash"))
        observations = _records(self.observations, "health observations")
        if not all(type(item) is HealthObservation for item in observations):
            raise OpsToolError("health observations must contain HealthObservation records")
        typed_observations = tuple(cast(HealthObservation, item) for item in observations)
        if tuple(item.kind for item in typed_observations) != tuple(HealthObservationKind):
            raise OpsToolError("health observations must exactly cover each safe health kind once")
        evidence_hashes = tuple(item.evidence_hash for item in typed_observations)
        if len(set(evidence_hashes)) != len(evidence_hashes):
            raise OpsToolError("health observations cannot contain duplicate evidence hashes")
        if type(self.kill_switch_state) is not KillSwitchObservationState:
            raise OpsToolError("kill_switch_state must be a KillSwitchObservationState")
        if type(self.risk_state) is not RiskObservationState:
            raise OpsToolError("risk_state must be a RiskObservationState")
        observed_at, available_at = _ordered_times(
            self.observed_at,
            self.available_at,
            label="health report",
        )
        if any(
            item.observed_at > observed_at or item.available_at > available_at
            for item in typed_observations
        ):
            raise OpsToolError("health observation timestamps cannot be later than the health report")
        object.__setattr__(self, "observations", typed_observations)
        object.__setattr__(self, "evidence_hashes", evidence_hashes)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "authorization_status", _authorized(self.authorization_status))


@dataclass(frozen=True, slots=True)
class OpsLogBucket:
    """One bounded, redacted log count; it deliberately carries no log text."""

    severity: LogSeverity
    code: OpsLogCode
    count: int
    evidence_hash: str
    observed_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        if type(self.severity) is not LogSeverity:
            raise OpsToolError("log severity must be a LogSeverity")
        if type(self.code) is not OpsLogCode:
            raise OpsToolError("log code must be an OpsLogCode")
        if type(self.count) is not int or not 1 <= self.count <= _MAX_LOG_BUCKET_COUNT:
            raise OpsToolError(
                f"log bucket count must be an integer between 1 and {_MAX_LOG_BUCKET_COUNT}"
            )
        object.__setattr__(self, "evidence_hash", _sha256(self.evidence_hash, "evidence_hash"))
        observed_at, available_at = _ordered_times(
            self.observed_at,
            self.available_at,
            label="log bucket",
        )
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class LogSummary:
    """A bounded summary of already-redacted logs, with no text, path, or endpoint fields."""

    scope_hash: str
    buckets: tuple[OpsLogBucket, ...]
    summary_evidence_hash: str
    observed_at: datetime
    available_at: datetime
    authorization_status: Literal["AUTHORIZED"]
    redaction_verified: Literal[True] = field(default=True, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_hash", _sha256(self.scope_hash, "scope_hash"))
        buckets = _records(self.buckets, "log buckets")
        if len(buckets) > _MAX_LOG_BUCKETS or not all(type(item) is OpsLogBucket for item in buckets):
            raise OpsToolError(
                f"log buckets must contain at most {_MAX_LOG_BUCKETS} OpsLogBucket records"
            )
        typed_buckets = tuple(cast(OpsLogBucket, item) for item in buckets)
        if len({(item.severity, item.code) for item in typed_buckets}) != len(typed_buckets):
            raise OpsToolError("log buckets cannot duplicate severity/code pairs")
        summary_evidence_hash = _sha256(self.summary_evidence_hash, "summary_evidence_hash")
        if summary_evidence_hash in {item.evidence_hash for item in typed_buckets}:
            raise OpsToolError("log summary evidence cannot duplicate a bucket evidence hash")
        observed_at, available_at = _ordered_times(
            self.observed_at,
            self.available_at,
            label="log summary",
        )
        if any(
            item.observed_at > observed_at or item.available_at > available_at
            for item in typed_buckets
        ):
            raise OpsToolError("log bucket timestamps cannot be later than the log summary")
        object.__setattr__(self, "buckets", typed_buckets)
        object.__setattr__(self, "summary_evidence_hash", summary_evidence_hash)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "authorization_status", _authorized(self.authorization_status))


@dataclass(frozen=True, slots=True)
class DeploymentDiagnosisSummary:
    """A closed deployment state and evidence hash, never a command or host diagnostic."""

    scope_hash: str
    state: DeploymentDiagnosisState
    evidence_hash: str
    observed_at: datetime
    available_at: datetime
    authorization_status: Literal["AUTHORIZED"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_hash", _sha256(self.scope_hash, "scope_hash"))
        if type(self.state) is not DeploymentDiagnosisState:
            raise OpsToolError("deployment state must be a DeploymentDiagnosisState")
        object.__setattr__(self, "evidence_hash", _sha256(self.evidence_hash, "evidence_hash"))
        observed_at, available_at = _ordered_times(
            self.observed_at,
            self.available_at,
            label="deployment diagnosis",
        )
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "authorization_status", _authorized(self.authorization_status))


@dataclass(frozen=True, slots=True)
class BackupStatusSummary:
    """A closed backup state and evidence hash, never a backup artifact or restore instruction."""

    scope_hash: str
    state: BackupStatusState
    evidence_hash: str
    observed_at: datetime
    available_at: datetime
    authorization_status: Literal["AUTHORIZED"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_hash", _sha256(self.scope_hash, "scope_hash"))
        if type(self.state) is not BackupStatusState:
            raise OpsToolError("backup state must be a BackupStatusState")
        object.__setattr__(self, "evidence_hash", _sha256(self.evidence_hash, "evidence_hash"))
        observed_at, available_at = _ordered_times(
            self.observed_at,
            self.available_at,
            label="backup status",
        )
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "authorization_status", _authorized(self.authorization_status))


@dataclass(frozen=True, slots=True)
class OpsSnapshotSummary:
    """One immutable, authorized, point-in-time operations projection for diagnostic use only."""

    scope_hash: str
    snapshot_hash: str
    health_report: HealthReportSummary
    log_summary: LogSummary
    deployment_diagnosis: DeploymentDiagnosisSummary
    backup_status: BackupStatusSummary
    observed_at: datetime
    available_at: datetime
    authorization_status: Literal["AUTHORIZED"]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        scope_hash = _sha256(self.scope_hash, "scope_hash")
        object.__setattr__(self, "scope_hash", scope_hash)
        object.__setattr__(self, "snapshot_hash", _sha256(self.snapshot_hash, "snapshot_hash"))
        sections = (
            ("health_report", self.health_report, HealthReportSummary),
            ("log_summary", self.log_summary, LogSummary),
            ("deployment_diagnosis", self.deployment_diagnosis, DeploymentDiagnosisSummary),
            ("backup_status", self.backup_status, BackupStatusSummary),
        )
        if not all(type(section) is expected for _, section, expected in sections):
            raise OpsToolError("ops snapshot must contain exactly the four typed safe sections")
        if any(section.scope_hash != scope_hash for _, section, _ in sections):
            raise OpsToolError("ops snapshot sections must bind the exact snapshot scope_hash")
        if any(section.authorization_status != "AUTHORIZED" for _, section, _ in sections):
            raise OpsToolError("ops snapshot sections must be AUTHORIZED")
        observed_at, available_at = _ordered_times(
            self.observed_at,
            self.available_at,
            label="ops snapshot",
        )
        if any(
            section.observed_at > observed_at or section.available_at > available_at
            for _, section, _ in sections
        ):
            raise OpsToolError("section evidence timestamps cannot be later than the ops snapshot")
        evidence_hashes = self._evidence_hashes()
        if len(set(evidence_hashes)) != len(evidence_hashes):
            raise OpsToolError("ops snapshot cannot contain duplicate evidence hashes")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "authorization_status", _authorized(self.authorization_status))

    def _evidence_hashes(self) -> tuple[str, ...]:
        """Return every opaque leaf-evidence hash without exposing source payloads."""

        return (
            *self.health_report.evidence_hashes,
            self.log_summary.summary_evidence_hash,
            *(item.evidence_hash for item in self.log_summary.buckets),
            self.deployment_diagnosis.evidence_hash,
            self.backup_status.evidence_hash,
        )


@dataclass(frozen=True, slots=True)
class InspectOpsSnapshotRequest:
    """Read one already-authorized snapshot visible at an explicit point in time.

    There is intentionally no endpoint, host, service, path, shell command,
    query, account, profile, or scope selector in this request.
    """

    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _time(self.as_of, "as_of"))


class OpsSnapshotCatalog(Protocol):
    """Trusted source of pre-sanitized immutable operations snapshots."""

    def inspect_ops_snapshot(
        self,
        *,
        request: InspectOpsSnapshotRequest,
    ) -> OpsSnapshotSummary: ...


@dataclass(frozen=True, slots=True)
class OpsToolDependencies:
    """Explicit injected capability; no global fallback or privileged discovery exists."""

    ops_snapshot_catalog: OpsSnapshotCatalog

    def __post_init__(self) -> None:
        try:
            inspection = self.ops_snapshot_catalog.inspect_ops_snapshot
        except AttributeError as exc:
            raise OpsToolError("ops tool dependencies are missing inspect_ops_snapshot") from exc
        if not callable(inspection):
            raise OpsToolError("ops snapshot catalog inspection must be callable")


OpsToolRequest: TypeAlias = InspectOpsSnapshotRequest
OpsToolResponse: TypeAlias = OpsSnapshotSummary


class TypedOpsToolApi:
    """The complete non-mutating operations tool surface for an AI caller."""

    __slots__ = ("_dependencies",)

    def __init__(self, dependencies: OpsToolDependencies) -> None:
        if type(dependencies) is not OpsToolDependencies:
            raise OpsToolError("dependencies must be OpsToolDependencies")
        self._dependencies = dependencies

    def invoke(self, tool_name: OpsToolName, request: OpsToolRequest) -> OpsToolResponse:
        """Inspect exactly one typed, authorized snapshot without retries or side effects."""

        if type(tool_name) is not OpsToolName:
            raise OpsToolError("tool_name must be an OpsToolName enum value")
        if tool_name is not OpsToolName.INSPECT_OPS_SNAPSHOT:
            raise OpsToolError("tool_name is outside the closed operations allowlist")
        if type(request) is not InspectOpsSnapshotRequest:
            raise OpsToolError("request must be exactly InspectOpsSnapshotRequest")
        result = self._dependencies.ops_snapshot_catalog.inspect_ops_snapshot(request=request)
        if type(result) is not OpsSnapshotSummary:
            raise OpsToolError("ops snapshot catalog must return OpsSnapshotSummary")
        if result.authorization_status != "AUTHORIZED":
            raise OpsToolError("ops snapshot result must be AUTHORIZED")
        if result.available_at > request.as_of:
            raise OpsToolError("ops snapshot is not available at the requested as_of")
        return result
