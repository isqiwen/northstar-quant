"""Pure, fail-closed candidate-evidence acceptance boundary.

This module records only already-produced opaque evidence.  It intentionally
does not infer a causal path between independent research, portfolio, and
simulation artifacts.  A successful evaluation is evidence-only and never
changes any external state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Literal, cast


__all__ = [
    "CandidateAcceptanceError",
    "CandidateAcceptanceRequest",
    "CandidateAcceptanceResult",
    "CandidateAcceptanceState",
    "CandidateAcceptanceVerifier",
    "CandidateEnvironment",
    "CandidateEvidenceLane",
    "CandidateEvidenceStatus",
    "CandidateLaneEvidence",
    "CandidateSeam",
    "CandidateSeamEvidence",
]


class CandidateAcceptanceError(ValueError):
    """Raised when candidate evidence is incomplete, ambiguous, or unsafe."""


class CandidateEnvironment(StrEnum):
    """The only non-live environments that can carry candidate evidence."""

    OFFLINE = "offline"
    PAPER = "paper"
    CTP_SIM = "ctp_sim"


class CandidateEvidenceStatus(StrEnum):
    """Closed verification outcomes; non-verified evidence always blocks."""

    VERIFIED = "verified"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class CandidateEvidenceLane(StrEnum):
    """Independent evidence lanes required for one candidate evaluation."""

    DATA_PIT = "data_pit"
    INTELLIGENCE = "intelligence"
    RESEARCH = "research"
    PORTFOLIO_RISK = "portfolio_risk"
    EXECUTION_SIMULATION = "execution_simulation"
    DEPLOYMENT = "deployment"
    MONITORING = "monitoring"
    BACKUP_RESTORE = "backup_restore"
    AI_SAFETY = "ai_safety"


class CandidateSeam(StrEnum):
    """The explicitly checked cross-lane seams, not implied causal authority."""

    DATA_PIT_TO_RESEARCH = "data_pit_to_research"
    INTELLIGENCE_TO_RESEARCH = "intelligence_to_research"
    RESEARCH_TO_PORTFOLIO_RISK = "research_to_portfolio_risk"
    PORTFOLIO_RISK_TO_EXECUTION_SIMULATION = "portfolio_risk_to_execution_simulation"


class CandidateAcceptanceState(StrEnum):
    """A result can be blocked or evidence-only; it never confers authority."""

    BLOCKED = "blocked"
    CANDIDATE_EVIDENCE_ONLY = "candidate_evidence_only"


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_LANE_INDEX = {lane: index for index, lane in enumerate(CandidateEvidenceLane)}
_SEAM_INDEX = {seam: index for index, seam in enumerate(CandidateSeam)}
_SEAM_ENDPOINTS: dict[
    CandidateSeam,
    tuple[CandidateEvidenceLane, CandidateEvidenceLane],
] = {
    CandidateSeam.DATA_PIT_TO_RESEARCH: (
        CandidateEvidenceLane.DATA_PIT,
        CandidateEvidenceLane.RESEARCH,
    ),
    CandidateSeam.INTELLIGENCE_TO_RESEARCH: (
        CandidateEvidenceLane.INTELLIGENCE,
        CandidateEvidenceLane.RESEARCH,
    ),
    CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK: (
        CandidateEvidenceLane.RESEARCH,
        CandidateEvidenceLane.PORTFOLIO_RISK,
    ),
    CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION: (
        CandidateEvidenceLane.PORTFOLIO_RISK,
        CandidateEvidenceLane.EXECUTION_SIMULATION,
    ),
}


def _identifier(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or value.strip() != value
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise CandidateAcceptanceError(f"{field_name} must be a non-empty stable identifier")
    return value


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise CandidateAcceptanceError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _utc_time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CandidateAcceptanceError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _records(value: object, field_name: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise CandidateAcceptanceError(f"{field_name} must be an iterable of typed records")
    return tuple(value)


def _hashes(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise CandidateAcceptanceError(f"{field_name} must be an iterable of SHA-256 hashes")
    normalized = tuple(_sha256(item, field_name) for item in value)
    if not normalized:
        raise CandidateAcceptanceError(f"{field_name} must contain at least one evidence hash")
    if len(set(normalized)) != len(normalized):
        raise CandidateAcceptanceError(f"{field_name} cannot contain duplicate evidence hashes")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class CandidateLaneEvidence:
    """One timestamped, opaque evidence lane with no source payload."""

    lane: CandidateEvidenceLane
    status: CandidateEvidenceStatus
    identity_hash: str
    evidence_hashes: tuple[str, ...]
    available_at: datetime

    def __post_init__(self) -> None:
        if type(self.lane) is not CandidateEvidenceLane:
            raise CandidateAcceptanceError("lane must be a CandidateEvidenceLane")
        if type(self.status) is not CandidateEvidenceStatus:
            raise CandidateAcceptanceError("status must be a CandidateEvidenceStatus")
        object.__setattr__(self, "identity_hash", _sha256(self.identity_hash, "identity_hash"))
        object.__setattr__(self, "evidence_hashes", _hashes(self.evidence_hashes, "evidence_hashes"))
        object.__setattr__(self, "available_at", _utc_time(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class CandidateSeamEvidence:
    """Opaque evidence that two independently recorded lane identities align."""

    seam: CandidateSeam
    status: CandidateEvidenceStatus
    source_identity_hash: str
    destination_identity_hash: str
    evidence_hashes: tuple[str, ...]
    available_at: datetime

    def __post_init__(self) -> None:
        if type(self.seam) is not CandidateSeam:
            raise CandidateAcceptanceError("seam must be a CandidateSeam")
        if type(self.status) is not CandidateEvidenceStatus:
            raise CandidateAcceptanceError("status must be a CandidateEvidenceStatus")
        object.__setattr__(
            self,
            "source_identity_hash",
            _sha256(self.source_identity_hash, "source_identity_hash"),
        )
        object.__setattr__(
            self,
            "destination_identity_hash",
            _sha256(self.destination_identity_hash, "destination_identity_hash"),
        )
        object.__setattr__(self, "evidence_hashes", _hashes(self.evidence_hashes, "evidence_hashes"))
        object.__setattr__(self, "available_at", _utc_time(self.available_at, "available_at"))


def _canonical_lanes(value: object, field_name: str) -> tuple[CandidateLaneEvidence, ...]:
    records = _records(value, field_name)
    if not all(type(item) is CandidateLaneEvidence for item in records):
        raise CandidateAcceptanceError(f"{field_name} must contain CandidateLaneEvidence records")
    lanes = tuple(cast(CandidateLaneEvidence, item) for item in records)
    if len(lanes) != len(CandidateEvidenceLane) or {
        item.lane for item in lanes
    } != set(CandidateEvidenceLane):
        raise CandidateAcceptanceError(f"{field_name} must exactly cover each evidence lane once")
    return tuple(sorted(lanes, key=lambda item: _LANE_INDEX[item.lane]))


def _canonical_seams(value: object, field_name: str) -> tuple[CandidateSeamEvidence, ...]:
    records = _records(value, field_name)
    if not all(type(item) is CandidateSeamEvidence for item in records):
        raise CandidateAcceptanceError(f"{field_name} must contain CandidateSeamEvidence records")
    seams = tuple(cast(CandidateSeamEvidence, item) for item in records)
    if len(seams) != len(CandidateSeam) or {
        item.seam for item in seams
    } != set(CandidateSeam):
        raise CandidateAcceptanceError(f"{field_name} must exactly cover each candidate seam once")
    return tuple(sorted(seams, key=lambda item: _SEAM_INDEX[item.seam]))


def _validate_visibility(
    *,
    lanes: tuple[CandidateLaneEvidence, ...],
    seams: tuple[CandidateSeamEvidence, ...],
    as_of: datetime,
) -> None:
    if any(item.available_at > as_of for item in lanes) or any(
        item.available_at > as_of for item in seams
    ):
        raise CandidateAcceptanceError("candidate evidence cannot be later than as_of")


def _validate_seam_bindings(
    *,
    lanes: tuple[CandidateLaneEvidence, ...],
    seams: tuple[CandidateSeamEvidence, ...],
) -> None:
    lane_by_kind = {item.lane: item for item in lanes}
    for seam in seams:
        source_lane, destination_lane = _SEAM_ENDPOINTS[seam.seam]
        if seam.source_identity_hash != lane_by_kind[source_lane].identity_hash:
            raise CandidateAcceptanceError(
                f"{seam.seam.value} source identity must match {source_lane.value}"
            )
        if seam.destination_identity_hash != lane_by_kind[destination_lane].identity_hash:
            raise CandidateAcceptanceError(
                f"{seam.seam.value} destination identity must match {destination_lane.value}"
            )
        if seam.status is CandidateEvidenceStatus.VERIFIED and (
            lane_by_kind[source_lane].status is not CandidateEvidenceStatus.VERIFIED
            or lane_by_kind[destination_lane].status is not CandidateEvidenceStatus.VERIFIED
        ):
            raise CandidateAcceptanceError(
                f"{seam.seam.value} cannot be VERIFIED when an endpoint lane is not VERIFIED"
            )


def _receipt_hash(
    *,
    candidate_id: str,
    environment: CandidateEnvironment,
    as_of: datetime,
    lanes: tuple[CandidateLaneEvidence, ...],
    seams: tuple[CandidateSeamEvidence, ...],
    state: CandidateAcceptanceState,
    blocking_lanes: tuple[CandidateEvidenceLane, ...],
    blocking_seams: tuple[CandidateSeam, ...],
) -> str:
    payload = {
        "as_of": as_of.isoformat(),
        "blocking_lanes": [item.value for item in blocking_lanes],
        "blocking_seams": [item.value for item in blocking_seams],
        "candidate_id": candidate_id,
        "eligible_for_trading": False,
        "environment": environment.value,
        "format": "northstar.candidate-acceptance.v1",
        "lanes": [
            {
                "available_at": item.available_at.isoformat(),
                "evidence_hashes": list(item.evidence_hashes),
                "identity_hash": item.identity_hash,
                "lane": item.lane.value,
                "status": item.status.value,
            }
            for item in lanes
        ],
        "seams": [
            {
                "available_at": item.available_at.isoformat(),
                "destination_identity_hash": item.destination_identity_hash,
                "evidence_hashes": list(item.evidence_hashes),
                "seam": item.seam.value,
                "source_identity_hash": item.source_identity_hash,
                "status": item.status.value,
            }
            for item in seams
        ],
        "state": state.value,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateAcceptanceRequest:
    """A complete, point-in-time set of independent candidate evidence."""

    candidate_id: str
    environment: CandidateEnvironment
    as_of: datetime
    lanes: tuple[CandidateLaneEvidence, ...]
    seams: tuple[CandidateSeamEvidence, ...]

    def __post_init__(self) -> None:
        candidate_id = _identifier(self.candidate_id, "candidate_id")
        if type(self.environment) is not CandidateEnvironment:
            raise CandidateAcceptanceError("environment must be a CandidateEnvironment")
        as_of = _utc_time(self.as_of, "as_of")
        lanes = _canonical_lanes(self.lanes, "lanes")
        seams = _canonical_seams(self.seams, "seams")
        _validate_visibility(lanes=lanes, seams=seams, as_of=as_of)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "lanes", lanes)
        object.__setattr__(self, "seams", seams)


@dataclass(frozen=True, slots=True)
class CandidateAcceptanceResult:
    """A deterministic evidence receipt that cannot grant trading eligibility."""

    candidate_id: str
    environment: CandidateEnvironment
    as_of: datetime
    lanes: tuple[CandidateLaneEvidence, ...]
    seams: tuple[CandidateSeamEvidence, ...]
    state: CandidateAcceptanceState
    blocking_lanes: tuple[CandidateEvidenceLane, ...]
    blocking_seams: tuple[CandidateSeam, ...]
    receipt_hash: str = field(init=False)
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        candidate_id = _identifier(self.candidate_id, "candidate_id")
        if type(self.environment) is not CandidateEnvironment:
            raise CandidateAcceptanceError("environment must be a CandidateEnvironment")
        as_of = _utc_time(self.as_of, "as_of")
        lanes = _canonical_lanes(self.lanes, "lanes")
        seams = _canonical_seams(self.seams, "seams")
        _validate_visibility(lanes=lanes, seams=seams, as_of=as_of)
        _validate_seam_bindings(lanes=lanes, seams=seams)
        if type(self.state) is not CandidateAcceptanceState:
            raise CandidateAcceptanceError("state must be a CandidateAcceptanceState")
        blocking_lanes = _canonical_blocking_lanes(self.blocking_lanes)
        blocking_seams = _canonical_blocking_seams(self.blocking_seams)
        expected_blocking_lanes = tuple(
            item.lane
            for item in lanes
            if item.status is not CandidateEvidenceStatus.VERIFIED
        )
        expected_blocking_seams = tuple(
            item.seam
            for item in seams
            if item.status is not CandidateEvidenceStatus.VERIFIED
        )
        if blocking_lanes != expected_blocking_lanes:
            raise CandidateAcceptanceError("blocking_lanes must exactly match non-verified lanes")
        if blocking_seams != expected_blocking_seams:
            raise CandidateAcceptanceError("blocking_seams must exactly match non-verified seams")
        expected_state = (
            CandidateAcceptanceState.BLOCKED
            if blocking_lanes or blocking_seams
            else CandidateAcceptanceState.CANDIDATE_EVIDENCE_ONLY
        )
        if self.state is not expected_state:
            raise CandidateAcceptanceError("state must exactly match the evidence blockers")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "lanes", lanes)
        object.__setattr__(self, "seams", seams)
        object.__setattr__(self, "blocking_lanes", blocking_lanes)
        object.__setattr__(self, "blocking_seams", blocking_seams)
        object.__setattr__(
            self,
            "receipt_hash",
            _receipt_hash(
                candidate_id=candidate_id,
                environment=self.environment,
                as_of=as_of,
                lanes=lanes,
                seams=seams,
                state=self.state,
                blocking_lanes=blocking_lanes,
                blocking_seams=blocking_seams,
            ),
        )


def _canonical_blocking_lanes(value: object) -> tuple[CandidateEvidenceLane, ...]:
    records = _records(value, "blocking_lanes")
    if not all(type(item) is CandidateEvidenceLane for item in records):
        raise CandidateAcceptanceError("blocking_lanes must contain CandidateEvidenceLane values")
    lanes = tuple(cast(CandidateEvidenceLane, item) for item in records)
    if len(set(lanes)) != len(lanes):
        raise CandidateAcceptanceError("blocking_lanes cannot contain duplicates")
    return tuple(sorted(lanes, key=lambda item: _LANE_INDEX[item]))


def _canonical_blocking_seams(value: object) -> tuple[CandidateSeam, ...]:
    records = _records(value, "blocking_seams")
    if not all(type(item) is CandidateSeam for item in records):
        raise CandidateAcceptanceError("blocking_seams must contain CandidateSeam values")
    seams = tuple(cast(CandidateSeam, item) for item in records)
    if len(set(seams)) != len(seams):
        raise CandidateAcceptanceError("blocking_seams cannot contain duplicates")
    return tuple(sorted(seams, key=lambda item: _SEAM_INDEX[item]))


class CandidateAcceptanceVerifier:
    """Evaluate one complete candidate-evidence request without side effects."""

    __slots__ = ()

    def evaluate(self, request: CandidateAcceptanceRequest) -> CandidateAcceptanceResult:
        """Return a deterministic, non-tradable evidence receipt."""

        if type(request) is not CandidateAcceptanceRequest:
            raise CandidateAcceptanceError("request must be a CandidateAcceptanceRequest")
        candidate_id = _identifier(request.candidate_id, "candidate_id")
        if type(request.environment) is not CandidateEnvironment:
            raise CandidateAcceptanceError("environment must be a CandidateEnvironment")
        as_of = _utc_time(request.as_of, "as_of")
        lanes = _canonical_lanes(request.lanes, "lanes")
        seams = _canonical_seams(request.seams, "seams")
        _validate_visibility(lanes=lanes, seams=seams, as_of=as_of)
        _validate_seam_bindings(lanes=lanes, seams=seams)
        blocking_lanes = tuple(
            item.lane
            for item in lanes
            if item.status is not CandidateEvidenceStatus.VERIFIED
        )
        blocking_seams = tuple(
            item.seam
            for item in seams
            if item.status is not CandidateEvidenceStatus.VERIFIED
        )
        state = (
            CandidateAcceptanceState.BLOCKED
            if blocking_lanes or blocking_seams
            else CandidateAcceptanceState.CANDIDATE_EVIDENCE_ONLY
        )
        return CandidateAcceptanceResult(
            candidate_id=candidate_id,
            environment=request.environment,
            as_of=as_of,
            lanes=lanes,
            seams=seams,
            state=state,
            blocking_lanes=blocking_lanes,
            blocking_seams=blocking_seams,
        )
