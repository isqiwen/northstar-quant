"""P10-WP05 fail-closed cases for the canonical P3 approval gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from northstar_quant.portfolio_risk.portfolio import (
    PortfolioRiskApprovalError,
    PortfolioRiskApprovalGate,
    PortfolioRiskReviewRequest,
    PortfolioRiskReviewStatus,
    PortfolioStressPolicy,
    StressScenarioLimit,
)
from northstar_quant.portfolio_risk.risk import RiskState, ScenarioKind, StressScenario
from tests.helpers.canonical_portfolio_risk import (
    build_approval_request,
    build_canonical_portfolio_risk_fixture,
)


def _evaluate(request: PortfolioRiskReviewRequest):
    return PortfolioRiskApprovalGate().evaluate(build_approval_request(request))


def _with_limit_threshold(threshold: float) -> PortfolioRiskReviewRequest:
    fixture = build_canonical_portfolio_risk_fixture()
    policy = replace(
        fixture.policy,
        limits=replace(fixture.policy.limits, per_contract=threshold),
    )
    return replace(fixture.review_request, policy=policy)


@pytest.mark.parametrize(
    ("case", "request_factory", "expected_status", "expected_reason"),
    [
        (
            "unknown-state",
            lambda fixture: replace(
                fixture.review_request,
                risk_state=replace(fixture.risk_state, state_snapshot=None),
            ),
            PortfolioRiskReviewStatus.UNKNOWN,
            "RISK_STATE_UNKNOWN",
        ),
        (
            "limit-warn",
            lambda fixture: _with_limit_threshold(0.16),
            PortfolioRiskReviewStatus.WARN,
            None,
        ),
        (
            "limit-block",
            lambda fixture: _with_limit_threshold(0.13),
            PortfolioRiskReviewStatus.BLOCK,
            None,
        ),
        (
            "halt",
            lambda fixture: replace(
                fixture.review_request,
                risk_state=replace(
                    fixture.risk_state,
                    state_snapshot=fixture.risk_state.state_snapshot.transition(
                        target=RiskState.HALT,
                        occurred_at=fixture.risk_state.state_snapshot.occurred_at
                        + timedelta(minutes=1),
                        reason="fixture-halt",
                    ),
                ),
            ),
            PortfolioRiskReviewStatus.BLOCK,
            "RISK_STATE_HALT",
        ),
    ],
)
def test_unknown_warn_block_or_halt_never_constructs_an_approved_target(
    case: str,
    request_factory,
    expected_status: PortfolioRiskReviewStatus,
    expected_reason: str | None,
) -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    request = request_factory(fixture)

    evidence = _evaluate(request)

    assert case
    assert evidence.review.status is expected_status
    assert evidence.approved_target is None
    assert f"REVIEW_{expected_status.value}" in evidence.rejection_reasons
    if expected_reason is not None:
        assert expected_reason in evidence.review.reason_codes
    assert evidence.eligible_for_execution is False
    assert evidence.eligible_for_broker_order is False


@pytest.mark.parametrize(("threshold", "expected_status"), [(0.04, "WARN"), (0.03, "BLOCK")])
def test_stress_warn_or_block_never_constructs_an_approved_target(
    threshold: float,
    expected_status: str,
) -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    scenario_limits = tuple(
        replace(item, max_loss_fraction=threshold)
        if item.scenario.kind is ScenarioKind.GAP
        else item
        for item in fixture.policy.stress_policy.scenario_limits
    )
    request = replace(
        fixture.review_request,
        policy=replace(
            fixture.policy,
            stress_policy=replace(
                fixture.policy.stress_policy,
                scenario_limits=scenario_limits,
            ),
        ),
    )

    evidence = _evaluate(request)
    gap = next(item for item in evidence.review.stress_checks if item.kind is ScenarioKind.GAP)

    assert gap.status.value == expected_status
    assert evidence.review.status.value == expected_status
    assert evidence.approved_target is None
    assert f"REVIEW_{expected_status}" in evidence.rejection_reasons


def test_rejects_missing_or_duplicate_stress_kinds_before_review() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    limits = fixture.policy.stress_policy.scenario_limits

    with pytest.raises(PortfolioRiskApprovalError, match="exactly one"):
        PortfolioStressPolicy(scenario_limits=limits[:-1])

    duplicate_gap = StressScenarioLimit(
        scenario=StressScenario(
            scenario_id="p10-duplicate-gap",
            kind=ScenarioKind.GAP,
            shock_fraction=0.1,
        ),
        max_loss_fraction=0.1,
        max_margin_utilization=0.2,
    )
    with pytest.raises(PortfolioRiskApprovalError, match="every ScenarioKind"):
        PortfolioStressPolicy(scenario_limits=(duplicate_gap, *limits[1:]))


def test_exact_composition_instrument_and_account_scope_are_required() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    request = fixture.review_request

    with pytest.raises(PortfolioRiskApprovalError, match="cover canonical portfolio positions exactly"):
        replace(request, instrument_snapshots=request.instrument_snapshots[:-1])

    mismatched_state = replace(fixture.risk_state, account_id="other-fixture-account")
    with pytest.raises(PortfolioRiskApprovalError, match="share account_id"):
        replace(request, risk_state=mismatched_state)

    with pytest.raises(PortfolioRiskApprovalError, match="composition must be an exact"):
        PortfolioRiskReviewRequest(
            composition=fixture.composition_fixture.evidence.portfolio_target,  # type: ignore[arg-type]
            account_snapshot=fixture.account_snapshot,
            instrument_snapshots=fixture.instrument_snapshots,
            risk_state=fixture.risk_state,
            policy=fixture.policy,
            evaluated_at=request.evaluated_at,
        )


def test_future_account_input_is_unknown_and_never_constructs_a_target() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    evaluated_at = fixture.review_request.evaluated_at
    future_account = replace(
        fixture.account_snapshot,
        observed_at=evaluated_at + timedelta(seconds=30),
        available_at=evaluated_at + timedelta(minutes=1),
        expires_at=evaluated_at + timedelta(minutes=5),
    )
    request = replace(fixture.review_request, account_snapshot=future_account)

    evidence = _evaluate(request)

    assert evidence.review.status is PortfolioRiskReviewStatus.UNKNOWN
    assert "ACCOUNT_SNAPSHOT_FUTURE" in evidence.review.reason_codes
    assert evidence.approved_target is None


def test_forged_early_or_stale_human_attestation_never_constructs_a_target() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    gate = PortfolioRiskApprovalGate()

    forged = gate.evaluate(
        build_approval_request(fixture.review_request, review_hash="0" * 64)
    )
    early = gate.evaluate(
        build_approval_request(
            fixture.review_request,
            approved_at=fixture.review.evaluated_at - timedelta(seconds=1),
        )
    )
    stale = gate.evaluate(
        build_approval_request(
            fixture.review_request,
            approved_at=fixture.review.approval_valid_until,
        )
    )

    assert forged.approved_target is None
    assert forged.rejection_reasons == ("ATTESTATION_REVIEW_HASH_MISMATCH",)
    assert early.approved_target is None
    assert early.rejection_reasons == ("ATTESTATION_BEFORE_REVIEW",)
    assert stale.approved_target is None
    assert stale.rejection_reasons == ("ATTESTATION_AFTER_INPUT_VALIDITY",)


def test_manually_forged_review_cannot_be_used_as_an_approval_artifact() -> None:
    fixture = build_canonical_portfolio_risk_fixture()

    with pytest.raises(PortfolioRiskApprovalError, match="must exactly replay"):
        replace(fixture.review, reason_codes=("FORGED_REVIEW",))


def test_tampered_composition_is_replayed_and_refused_before_derivation() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    composition = fixture.composition_fixture.evidence
    object.__setattr__(composition, "composition_hash", "0" * 64)
    request = replace(fixture.review_request, composition=composition)

    with pytest.raises(PortfolioRiskApprovalError, match="composition evidence"):
        PortfolioRiskApprovalGate().review(request)
