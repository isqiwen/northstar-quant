"""Public P8 candidate-evidence acceptance contract.

Candidate acceptance can only record a deterministic, point-in-time evidence
receipt.  Even complete evidence remains non-tradable and grants no control
or promotion authority.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
import inspect
from typing import Literal, get_args, get_origin, get_type_hints

import pytest

import northstar_quant.application.candidate_acceptance as candidate_acceptance
from northstar_quant.application.candidate_acceptance import (
    CandidateAcceptanceError,
    CandidateAcceptanceRequest,
    CandidateAcceptanceResult,
    CandidateAcceptanceState,
    CandidateAcceptanceVerifier,
    CandidateEnvironment,
    CandidateEvidenceLane,
    CandidateEvidenceStatus,
    CandidateLaneEvidence,
    CandidateSeam,
    CandidateSeamEvidence,
)


_AS_OF = datetime(2026, 8, 23, 9, 30, tzinfo=UTC)
_SEAM_ENDPOINTS = {
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


def _hash(value: int) -> str:
    return f"{value:064x}"


def _field_names(dto: type[object]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(dto))


def _lanes(
    *,
    statuses: dict[CandidateEvidenceLane, CandidateEvidenceStatus] | None = None,
    available_at: datetime = _AS_OF,
) -> tuple[CandidateLaneEvidence, ...]:
    statuses = statuses or {}
    return tuple(
        CandidateLaneEvidence(
            lane=lane,
            status=statuses.get(lane, CandidateEvidenceStatus.VERIFIED),
            identity_hash=_hash(index + 1),
            evidence_hashes=(_hash(index + 101),),
            available_at=available_at,
        )
        for index, lane in enumerate(CandidateEvidenceLane)
    )


def _seams(
    lanes: tuple[CandidateLaneEvidence, ...],
    *,
    statuses: dict[CandidateSeam, CandidateEvidenceStatus] | None = None,
    available_at: datetime = _AS_OF,
) -> tuple[CandidateSeamEvidence, ...]:
    statuses = statuses or {}
    by_lane = {item.lane: item for item in lanes}
    return tuple(
        CandidateSeamEvidence(
            seam=seam,
            status=statuses.get(seam, CandidateEvidenceStatus.VERIFIED),
            source_identity_hash=by_lane[_SEAM_ENDPOINTS[seam][0]].identity_hash,
            destination_identity_hash=by_lane[_SEAM_ENDPOINTS[seam][1]].identity_hash,
            evidence_hashes=(_hash(index + 201),),
            available_at=available_at,
        )
        for index, seam in enumerate(CandidateSeam)
    )


def _request(
    *,
    lane_statuses: dict[CandidateEvidenceLane, CandidateEvidenceStatus] | None = None,
    seam_statuses: dict[CandidateSeam, CandidateEvidenceStatus] | None = None,
    as_of: datetime = _AS_OF,
) -> CandidateAcceptanceRequest:
    lanes = _lanes(statuses=lane_statuses)
    seams = _seams(lanes, statuses=seam_statuses)
    return CandidateAcceptanceRequest(
        candidate_id="candidate.copper.1",
        environment=CandidateEnvironment.CTP_SIM,
        as_of=as_of,
        lanes=tuple(reversed(lanes)),
        seams=tuple(reversed(seams)),
    )


def test_candidate_acceptance_has_exact_closed_non_live_enums() -> None:
    for enum_type in (
        CandidateEnvironment,
        CandidateEvidenceStatus,
        CandidateEvidenceLane,
        CandidateSeam,
        CandidateAcceptanceState,
    ):
        assert issubclass(enum_type, str)
        assert issubclass(enum_type, Enum)

    assert tuple((item.name, item.value) for item in CandidateEnvironment) == (
        ("OFFLINE", "offline"),
        ("PAPER", "paper"),
        ("CTP_SIM", "ctp_sim"),
    )
    assert tuple((item.name, item.value) for item in CandidateEvidenceStatus) == (
        ("VERIFIED", "verified"),
        ("BLOCKED", "blocked"),
        ("UNKNOWN", "unknown"),
    )
    assert tuple((item.name, item.value) for item in CandidateEvidenceLane) == (
        ("DATA_PIT", "data_pit"),
        ("INTELLIGENCE", "intelligence"),
        ("RESEARCH", "research"),
        ("PORTFOLIO_RISK", "portfolio_risk"),
        ("EXECUTION_SIMULATION", "execution_simulation"),
        ("DEPLOYMENT", "deployment"),
        ("MONITORING", "monitoring"),
        ("BACKUP_RESTORE", "backup_restore"),
        ("AI_SAFETY", "ai_safety"),
    )
    assert tuple((item.name, item.value) for item in CandidateSeam) == (
        ("DATA_PIT_TO_RESEARCH", "data_pit_to_research"),
        ("INTELLIGENCE_TO_RESEARCH", "intelligence_to_research"),
        ("RESEARCH_TO_PORTFOLIO_RISK", "research_to_portfolio_risk"),
        (
            "PORTFOLIO_RISK_TO_EXECUTION_SIMULATION",
            "portfolio_risk_to_execution_simulation",
        ),
    )
    assert tuple((item.name, item.value) for item in CandidateAcceptanceState) == (
        ("BLOCKED", "blocked"),
        ("CANDIDATE_EVIDENCE_ONLY", "candidate_evidence_only"),
    )


def test_candidate_acceptance_public_api_is_exactly_evidence_only() -> None:
    assert candidate_acceptance.__all__ == [
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
    assert issubclass(CandidateAcceptanceError, ValueError)

    signature = inspect.signature(CandidateAcceptanceVerifier)
    assert tuple(signature.parameters) == ()
    public_callables = {
        name
        for name, value in inspect.getmembers(
            CandidateAcceptanceVerifier,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    assert public_callables == {"evaluate"}

    evaluate_hints = get_type_hints(CandidateAcceptanceVerifier.evaluate)
    assert evaluate_hints["request"] is CandidateAcceptanceRequest
    assert evaluate_hints["return"] is CandidateAcceptanceResult
    assert tuple(inspect.signature(CandidateAcceptanceVerifier.evaluate).parameters) == (
        "self",
        "request",
    )


def test_candidate_acceptance_dtos_are_frozen_slotted_and_exactly_hash_only() -> None:
    for dto in (
        CandidateLaneEvidence,
        CandidateSeamEvidence,
        CandidateAcceptanceRequest,
        CandidateAcceptanceResult,
    ):
        assert is_dataclass(dto)
        assert dto.__dataclass_params__.frozen is True
        assert hasattr(dto, "__slots__")

    assert _field_names(CandidateLaneEvidence) == (
        "lane",
        "status",
        "identity_hash",
        "evidence_hashes",
        "available_at",
    )
    assert _field_names(CandidateSeamEvidence) == (
        "seam",
        "status",
        "source_identity_hash",
        "destination_identity_hash",
        "evidence_hashes",
        "available_at",
    )
    assert _field_names(CandidateAcceptanceRequest) == (
        "candidate_id",
        "environment",
        "as_of",
        "lanes",
        "seams",
    )
    assert _field_names(CandidateAcceptanceResult) == (
        "candidate_id",
        "environment",
        "as_of",
        "lanes",
        "seams",
        "state",
        "blocking_lanes",
        "blocking_seams",
        "receipt_hash",
        "eligible_for_trading",
    )

    lane_hints = get_type_hints(CandidateLaneEvidence)
    seam_hints = get_type_hints(CandidateSeamEvidence)
    request_hints = get_type_hints(CandidateAcceptanceRequest)
    result_hints = get_type_hints(CandidateAcceptanceResult)
    result_fields = {field.name: field for field in fields(CandidateAcceptanceResult)}
    assert lane_hints["lane"] is CandidateEvidenceLane
    assert lane_hints["status"] is CandidateEvidenceStatus
    assert get_origin(lane_hints["evidence_hashes"]) is tuple
    assert get_args(lane_hints["evidence_hashes"]) == (str, Ellipsis)
    assert seam_hints["seam"] is CandidateSeam
    assert seam_hints["status"] is CandidateEvidenceStatus
    assert request_hints["environment"] is CandidateEnvironment
    assert get_args(request_hints["lanes"]) == (CandidateLaneEvidence, Ellipsis)
    assert get_args(request_hints["seams"]) == (CandidateSeamEvidence, Ellipsis)
    assert result_hints["state"] is CandidateAcceptanceState
    assert get_args(result_hints["blocking_lanes"]) == (
        CandidateEvidenceLane,
        Ellipsis,
    )
    assert get_args(result_hints["blocking_seams"]) == (CandidateSeam, Ellipsis)
    assert get_origin(result_hints["eligible_for_trading"]) is Literal
    assert get_args(result_hints["eligible_for_trading"]) == (False,)
    assert result_fields["receipt_hash"].init is False
    assert result_fields["eligible_for_trading"].default is False
    assert result_fields["eligible_for_trading"].init is False


def test_verified_complete_evidence_is_canonical_deterministic_and_still_non_tradable() -> None:
    verifier = CandidateAcceptanceVerifier()
    first = verifier.evaluate(_request())
    second = verifier.evaluate(_request())

    assert first.state is CandidateAcceptanceState.CANDIDATE_EVIDENCE_ONLY
    assert first.blocking_lanes == ()
    assert first.blocking_seams == ()
    assert first.eligible_for_trading is False
    assert second.eligible_for_trading is False
    assert first.receipt_hash == second.receipt_hash
    assert tuple(item.lane for item in first.lanes) == tuple(CandidateEvidenceLane)
    assert tuple(item.seam for item in first.seams) == tuple(CandidateSeam)


@pytest.mark.parametrize(
    "status",
    (CandidateEvidenceStatus.BLOCKED, CandidateEvidenceStatus.UNKNOWN),
)
def test_non_verified_lane_fails_closed_without_any_trading_upgrade(
    status: CandidateEvidenceStatus,
) -> None:
    request = _request(
        lane_statuses={CandidateEvidenceLane.DATA_PIT: status},
        seam_statuses={CandidateSeam.DATA_PIT_TO_RESEARCH: status},
    )

    result = CandidateAcceptanceVerifier().evaluate(request)

    assert result.state is CandidateAcceptanceState.BLOCKED
    assert result.blocking_lanes == (CandidateEvidenceLane.DATA_PIT,)
    assert result.blocking_seams == (CandidateSeam.DATA_PIT_TO_RESEARCH,)
    assert result.eligible_for_trading is False


def test_verified_intelligence_to_research_seam_does_not_imply_other_bridges() -> None:
    """The real P8 seam evidence cannot synthetically verify the remaining seams."""

    remaining_blocked = {
        CandidateSeam.DATA_PIT_TO_RESEARCH: CandidateEvidenceStatus.BLOCKED,
        CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK: CandidateEvidenceStatus.BLOCKED,
        CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION: CandidateEvidenceStatus.BLOCKED,
    }

    result = CandidateAcceptanceVerifier().evaluate(
        _request(seam_statuses=remaining_blocked)
    )

    intelligence_to_research = next(
        evidence
        for evidence in result.seams
        if evidence.seam is CandidateSeam.INTELLIGENCE_TO_RESEARCH
    )
    assert intelligence_to_research.status is CandidateEvidenceStatus.VERIFIED
    assert result.state is CandidateAcceptanceState.BLOCKED
    assert result.blocking_lanes == ()
    assert result.blocking_seams == tuple(remaining_blocked)
    assert result.eligible_for_trading is False


def test_candidate_acceptance_rejects_incomplete_future_and_misaligned_evidence() -> None:
    lanes = _lanes()
    seams = _seams(lanes)
    with pytest.raises(CandidateAcceptanceError, match="environment must be"):
        CandidateAcceptanceRequest(
            candidate_id="candidate.copper.1",
            environment="ctp",  # type: ignore[arg-type]
            as_of=_AS_OF,
            lanes=lanes,
            seams=seams,
        )
    with pytest.raises(CandidateAcceptanceError, match="exactly cover each evidence lane"):
        CandidateAcceptanceRequest(
            candidate_id="candidate.copper.1",
            environment=CandidateEnvironment.OFFLINE,
            as_of=_AS_OF,
            lanes=lanes[:-1],
            seams=seams,
        )
    with pytest.raises(CandidateAcceptanceError, match="cannot be later than as_of"):
        CandidateAcceptanceRequest(
            candidate_id="candidate.copper.1",
            environment=CandidateEnvironment.PAPER,
            as_of=_AS_OF,
            lanes=_lanes(available_at=_AS_OF + timedelta(seconds=1)),
            seams=seams,
        )

    mismatched = CandidateSeamEvidence(
        seam=CandidateSeam.DATA_PIT_TO_RESEARCH,
        status=CandidateEvidenceStatus.VERIFIED,
        source_identity_hash=_hash(999),
        destination_identity_hash=next(
            item.identity_hash
            for item in lanes
            if item.lane is CandidateEvidenceLane.RESEARCH
        ),
        evidence_hashes=(_hash(999),),
        available_at=_AS_OF,
    )
    request = CandidateAcceptanceRequest(
        candidate_id="candidate.copper.1",
        environment=CandidateEnvironment.CTP_SIM,
        as_of=_AS_OF,
        lanes=lanes,
        seams=(mismatched, *seams[1:]),
    )
    with pytest.raises(CandidateAcceptanceError, match="source identity"):
        CandidateAcceptanceVerifier().evaluate(request)


def test_verified_seam_cannot_mask_an_unknown_endpoint_or_forge_an_unblocked_result() -> None:
    lanes = _lanes(
        statuses={CandidateEvidenceLane.DATA_PIT: CandidateEvidenceStatus.UNKNOWN}
    )
    seams = _seams(lanes)
    request = CandidateAcceptanceRequest(
        candidate_id="candidate.copper.1",
        environment=CandidateEnvironment.CTP_SIM,
        as_of=_AS_OF,
        lanes=lanes,
        seams=seams,
    )
    with pytest.raises(CandidateAcceptanceError, match="cannot be VERIFIED"):
        CandidateAcceptanceVerifier().evaluate(request)

    verified = _request()
    with pytest.raises(CandidateAcceptanceError, match="state must exactly match"):
        CandidateAcceptanceResult(
            candidate_id=verified.candidate_id,
            environment=verified.environment,
            as_of=verified.as_of,
            lanes=verified.lanes,
            seams=verified.seams,
            state=CandidateAcceptanceState.BLOCKED,
            blocking_lanes=(),
            blocking_seams=(),
        )


def test_no_request_or_result_input_can_enable_trading() -> None:
    request = _request()
    result = CandidateAcceptanceVerifier().evaluate(request)

    assert "eligible_for_trading" not in inspect.signature(CandidateAcceptanceRequest).parameters
    assert "eligible_for_trading" not in inspect.signature(CandidateAcceptanceResult).parameters
    assert result.eligible_for_trading is False
    with pytest.raises(TypeError):
        CandidateAcceptanceResult(
            candidate_id=result.candidate_id,
            environment=result.environment,
            as_of=result.as_of,
            lanes=result.lanes,
            seams=result.seams,
            state=result.state,
            blocking_lanes=result.blocking_lanes,
            blocking_seams=result.blocking_seams,
            eligible_for_trading=True,
        )
