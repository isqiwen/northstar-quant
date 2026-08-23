"""Unit coverage for the pure P8 candidate-evidence acceptance evaluator."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.application.candidate_acceptance import (
    CandidateAcceptanceError,
    CandidateAcceptanceRequest,
    CandidateAcceptanceState,
    CandidateAcceptanceVerifier,
    CandidateEnvironment,
    CandidateEvidenceLane,
    CandidateEvidenceStatus,
    CandidateLaneEvidence,
    CandidateSeam,
    CandidateSeamEvidence,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
_SEAM_LANES = {
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


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _lanes(
    statuses: dict[CandidateEvidenceLane, CandidateEvidenceStatus] | None = None,
) -> tuple[CandidateLaneEvidence, ...]:
    by_lane = statuses or {}
    return tuple(
        CandidateLaneEvidence(
            lane=lane,
            status=by_lane.get(lane, CandidateEvidenceStatus.VERIFIED),
            identity_hash=_hash(f"identity:{lane.value}"),
            evidence_hashes=(_hash(f"evidence:{lane.value}:b"), _hash(f"evidence:{lane.value}:a")),
            available_at=NOW - timedelta(minutes=2),
        )
        for lane in CandidateEvidenceLane
    )


def _seams(
    lanes: tuple[CandidateLaneEvidence, ...],
    statuses: dict[CandidateSeam, CandidateEvidenceStatus] | None = None,
) -> tuple[CandidateSeamEvidence, ...]:
    by_lane = {item.lane: item for item in lanes}
    by_seam = statuses or {}
    return tuple(
        CandidateSeamEvidence(
            seam=seam,
            status=by_seam.get(seam, CandidateEvidenceStatus.VERIFIED),
            source_identity_hash=by_lane[_SEAM_LANES[seam][0]].identity_hash,
            destination_identity_hash=by_lane[_SEAM_LANES[seam][1]].identity_hash,
            evidence_hashes=(_hash(f"evidence:{seam.value}"),),
            available_at=NOW - timedelta(minutes=1),
        )
        for seam in CandidateSeam
    )


def _request(
    *,
    lanes: tuple[CandidateLaneEvidence, ...] | None = None,
    seams: tuple[CandidateSeamEvidence, ...] | None = None,
    as_of: datetime = NOW,
) -> CandidateAcceptanceRequest:
    candidate_lanes = lanes or _lanes()
    return CandidateAcceptanceRequest(
        candidate_id="p8-candidate-001",
        environment=CandidateEnvironment.CTP_SIM,
        as_of=as_of,
        lanes=candidate_lanes,
        seams=seams or _seams(candidate_lanes),
    )


def test_verifier_returns_a_deterministic_non_tradable_evidence_only_receipt() -> None:
    request = _request()
    verifier = CandidateAcceptanceVerifier()

    first = verifier.evaluate(request)
    second = verifier.evaluate(request)

    assert first == second
    assert first.receipt_hash == second.receipt_hash
    assert first.state is CandidateAcceptanceState.CANDIDATE_EVIDENCE_ONLY
    assert first.blocking_lanes == ()
    assert first.blocking_seams == ()
    assert first.eligible_for_trading is False
    assert tuple(item.lane for item in first.lanes) == tuple(CandidateEvidenceLane)
    assert tuple(item.seam for item in first.seams) == tuple(CandidateSeam)
    assert all(item.evidence_hashes == tuple(sorted(item.evidence_hashes)) for item in first.lanes)
    assert not hasattr(first, "__dict__")
    with pytest.raises(FrozenInstanceError):
        first.candidate_id = "mutated"  # type: ignore[misc]


def test_enums_dtos_and_verifier_surface_are_closed_and_minimal() -> None:
    assert tuple(CandidateEnvironment) == (
        CandidateEnvironment.OFFLINE,
        CandidateEnvironment.PAPER,
        CandidateEnvironment.CTP_SIM,
    )
    assert tuple(CandidateEvidenceStatus) == (
        CandidateEvidenceStatus.VERIFIED,
        CandidateEvidenceStatus.BLOCKED,
        CandidateEvidenceStatus.UNKNOWN,
    )
    assert tuple(CandidateEvidenceLane) == (
        CandidateEvidenceLane.DATA_PIT,
        CandidateEvidenceLane.INTELLIGENCE,
        CandidateEvidenceLane.RESEARCH,
        CandidateEvidenceLane.PORTFOLIO_RISK,
        CandidateEvidenceLane.EXECUTION_SIMULATION,
        CandidateEvidenceLane.DEPLOYMENT,
        CandidateEvidenceLane.MONITORING,
        CandidateEvidenceLane.BACKUP_RESTORE,
        CandidateEvidenceLane.AI_SAFETY,
    )
    assert tuple(CandidateSeam) == (
        CandidateSeam.DATA_PIT_TO_RESEARCH,
        CandidateSeam.INTELLIGENCE_TO_RESEARCH,
        CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK,
        CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION,
    )
    assert tuple(CandidateAcceptanceState) == (
        CandidateAcceptanceState.BLOCKED,
        CandidateAcceptanceState.CANDIDATE_EVIDENCE_ONLY,
    )
    assert tuple(field.name for field in fields(CandidateAcceptanceRequest)) == (
        "candidate_id",
        "environment",
        "as_of",
        "lanes",
        "seams",
    )
    result_fields = fields(type(CandidateAcceptanceVerifier().evaluate(_request())))
    assert tuple(field.name for field in result_fields) == (
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
    assert result_fields[-2].init is False
    assert result_fields[-1].init is False
    assert {
        name for name in CandidateAcceptanceVerifier.__dict__ if not name.startswith("_")
    } == {"evaluate"}


def test_request_and_result_canonicalize_input_order_for_a_stable_receipt() -> None:
    base_lanes = _lanes()
    base_seams = _seams(base_lanes)

    canonical = CandidateAcceptanceVerifier().evaluate(_request(lanes=base_lanes, seams=base_seams))
    unordered = CandidateAcceptanceVerifier().evaluate(
        _request(lanes=tuple(reversed(base_lanes)), seams=tuple(reversed(base_seams)))
    )

    assert unordered.lanes == canonical.lanes
    assert unordered.seams == canonical.seams
    assert unordered.receipt_hash == canonical.receipt_hash


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CandidateLaneEvidence(
            lane="data_pit",  # type: ignore[arg-type]
            status=CandidateEvidenceStatus.VERIFIED,
            identity_hash=_hash("identity"),
            evidence_hashes=(_hash("evidence"),),
            available_at=NOW,
        ),
        lambda: CandidateLaneEvidence(
            lane=CandidateEvidenceLane.DATA_PIT,
            status="verified",  # type: ignore[arg-type]
            identity_hash=_hash("identity"),
            evidence_hashes=(_hash("evidence"),),
            available_at=NOW,
        ),
        lambda: CandidateLaneEvidence(
            lane=CandidateEvidenceLane.DATA_PIT,
            status=CandidateEvidenceStatus.VERIFIED,
            identity_hash="not-a-hash",
            evidence_hashes=(_hash("evidence"),),
            available_at=NOW,
        ),
        lambda: CandidateLaneEvidence(
            lane=CandidateEvidenceLane.DATA_PIT,
            status=CandidateEvidenceStatus.VERIFIED,
            identity_hash=_hash("identity"),
            evidence_hashes=(),
            available_at=NOW,
        ),
        lambda: CandidateLaneEvidence(
            lane=CandidateEvidenceLane.DATA_PIT,
            status=CandidateEvidenceStatus.VERIFIED,
            identity_hash=_hash("identity"),
            evidence_hashes=(_hash("evidence"), _hash("evidence")),
            available_at=NOW,
        ),
        lambda: CandidateSeamEvidence(
            seam="data_pit_to_research",  # type: ignore[arg-type]
            status=CandidateEvidenceStatus.VERIFIED,
            source_identity_hash=_hash("source"),
            destination_identity_hash=_hash("destination"),
            evidence_hashes=(_hash("evidence"),),
            available_at=NOW,
        ),
    ],
)
def test_evidence_dtos_reject_bad_enum_hash_and_duplicate_evidence_inputs(factory) -> None:
    with pytest.raises(CandidateAcceptanceError):
        factory()


def test_request_rejects_bad_environment_identifier_naive_and_future_evidence() -> None:
    with pytest.raises(CandidateAcceptanceError, match="CandidateEnvironment"):
        CandidateAcceptanceRequest(
            candidate_id="p8-candidate-001",
            environment="ctp_sim",  # type: ignore[arg-type]
            as_of=NOW,
            lanes=_lanes(),
            seams=_seams(_lanes()),
        )
    for candidate_id in ("unsafe/path", " p8-candidate-001", "p8-candidate-001 "):
        with pytest.raises(CandidateAcceptanceError, match="candidate_id"):
            CandidateAcceptanceRequest(
                candidate_id=candidate_id,
                environment=CandidateEnvironment.OFFLINE,
                as_of=NOW,
                lanes=_lanes(),
                seams=_seams(_lanes()),
            )
    with pytest.raises(CandidateAcceptanceError, match="timezone-aware"):
        _request(as_of=NOW.replace(tzinfo=None))

    lanes = _lanes()
    future_lanes = (*lanes[:-1], replace(lanes[-1], available_at=NOW + timedelta(seconds=1)))
    with pytest.raises(CandidateAcceptanceError, match="later than as_of"):
        _request(lanes=future_lanes, seams=_seams(future_lanes))
    seams = _seams(lanes)
    future_seams = (*seams[:-1], replace(seams[-1], available_at=NOW + timedelta(seconds=1)))
    with pytest.raises(CandidateAcceptanceError, match="later than as_of"):
        _request(lanes=lanes, seams=future_seams)


@pytest.mark.parametrize(
    ("lanes", "seams", "match"),
    [
        (lambda lanes, seams: lanes[:-1], lambda lanes, seams: seams, "evidence lane"),
        (
            lambda lanes, seams: (*lanes[:-1], lanes[0]),
            lambda lanes, seams: seams,
            "evidence lane",
        ),
        (lambda lanes, seams: lanes, lambda lanes, seams: seams[:-1], "candidate seam"),
        (
            lambda lanes, seams: lanes,
            lambda lanes, seams: (*seams[:-1], seams[0]),
            "candidate seam",
        ),
    ],
)
def test_request_requires_each_lane_and_seam_exactly_once(lanes, seams, match: str) -> None:
    valid_lanes = _lanes()
    valid_seams = _seams(valid_lanes)
    with pytest.raises(CandidateAcceptanceError, match=match):
        _request(lanes=lanes(valid_lanes, valid_seams), seams=seams(valid_lanes, valid_seams))


@pytest.mark.parametrize("attribute", ("source_identity_hash", "destination_identity_hash"))
def test_verifier_rejects_seams_that_do_not_bind_the_expected_lane_identity(attribute: str) -> None:
    lanes = _lanes()
    seams = _seams(lanes)
    if attribute == "source_identity_hash":
        replacement = replace(seams[0], source_identity_hash=_hash("wrong:source"))
    else:
        replacement = replace(seams[0], destination_identity_hash=_hash("wrong:destination"))
    request = _request(lanes=lanes, seams=(replacement, *seams[1:]))

    with pytest.raises(CandidateAcceptanceError, match="identity must match"):
        CandidateAcceptanceVerifier().evaluate(request)


def test_verifier_rejects_a_verified_seam_when_an_endpoint_lane_is_not_verified() -> None:
    lanes = _lanes({CandidateEvidenceLane.DATA_PIT: CandidateEvidenceStatus.UNKNOWN})
    request = _request(lanes=lanes, seams=_seams(lanes))

    with pytest.raises(CandidateAcceptanceError, match="cannot be VERIFIED"):
        CandidateAcceptanceVerifier().evaluate(request)


def test_unknown_and_blocked_evidence_produce_sorted_blockers_without_trading_eligibility() -> None:
    lanes = _lanes(
        {
            CandidateEvidenceLane.DEPLOYMENT: CandidateEvidenceStatus.BLOCKED,
            CandidateEvidenceLane.AI_SAFETY: CandidateEvidenceStatus.UNKNOWN,
        }
    )
    seams = _seams(
        lanes,
        {
            CandidateSeam.DATA_PIT_TO_RESEARCH: CandidateEvidenceStatus.UNKNOWN,
            CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK: CandidateEvidenceStatus.BLOCKED,
        },
    )

    result = CandidateAcceptanceVerifier().evaluate(_request(lanes=lanes, seams=seams))

    assert result.state is CandidateAcceptanceState.BLOCKED
    assert result.blocking_lanes == (
        CandidateEvidenceLane.DEPLOYMENT,
        CandidateEvidenceLane.AI_SAFETY,
    )
    assert result.blocking_seams == (
        CandidateSeam.DATA_PIT_TO_RESEARCH,
        CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK,
    )
    assert result.eligible_for_trading is False


def test_result_rejects_incorrect_blockers_or_state_and_verifier_rejects_wrong_request_type() -> None:
    request = _request()
    result = CandidateAcceptanceVerifier().evaluate(request)
    with pytest.raises(CandidateAcceptanceError, match="state must exactly"):
        replace(result, state=CandidateAcceptanceState.BLOCKED)
    with pytest.raises(CandidateAcceptanceError, match="blocking_lanes"):
        replace(result, blocking_lanes=(CandidateEvidenceLane.AI_SAFETY,))
    with pytest.raises(CandidateAcceptanceError, match="CandidateAcceptanceRequest"):
        CandidateAcceptanceVerifier().evaluate(object())  # type: ignore[arg-type]


def test_verifier_rechecks_a_request_before_issuing_a_receipt() -> None:
    request = _request()
    object.__setattr__(request, "lanes", request.lanes[:-1])

    with pytest.raises(CandidateAcceptanceError, match="evidence lane"):
        CandidateAcceptanceVerifier().evaluate(request)
