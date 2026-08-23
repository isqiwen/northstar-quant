"""P10-WP05 unit coverage for canonical portfolio-risk review derivation."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from northstar_quant.portfolio_risk.limits import LimitStatus
from northstar_quant.portfolio_risk.portfolio import (
    PortfolioRiskApprovalGate,
    PortfolioRiskReviewStatus,
)
from northstar_quant.portfolio_risk.risk import RiskState, ScenarioKind
from tests.helpers.canonical_portfolio_risk import (
    build_canonical_portfolio_risk_fixture,
)


def test_canonical_review_derives_exact_exposure_limits_stress_and_named_approval() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    review = fixture.review
    evidence = fixture.approval_evidence

    assert review.status is PortfolioRiskReviewStatus.PASS
    assert review.eligible_for_approval is True
    assert review.observed_risk_state is RiskState.NORMAL
    assert tuple(item.instrument_id for item in review.positions) == (
        "SHFE.AU2610",
        "SHFE.CU2610",
        "SHFE.RB2610",
    )
    assert tuple(item.target_weight for item in review.positions) == pytest.approx((0.12, 0.1, 0.14))
    assert tuple(item.notional for item in review.positions) == pytest.approx(
        (120_000.0, 100_000.0, 140_000.0)
    )
    assert review.exposure.gross == pytest.approx(360_000.0)
    assert review.exposure.net == pytest.approx(360_000.0)
    assert review.exposure.margin_required == pytest.approx(36_400.0)
    assert dict(review.exposure.by_exchange) == {"SHFE": pytest.approx(360_000.0)}
    assert review.exposure.concentration == pytest.approx(140_000.0 / 360_000.0)

    assert review.measurements.contract == pytest.approx(0.14)
    assert review.measurements.commodity == pytest.approx(0.14)
    assert review.measurements.sector == pytest.approx(0.14)
    assert review.measurements.exchange == pytest.approx(0.36)
    assert review.measurements.strategy == pytest.approx(0.3)
    assert review.measurements.account == pytest.approx(0.72)
    assert review.measurements.gross_leverage == pytest.approx(0.36)
    assert review.measurements.net_leverage == pytest.approx(0.36)
    assert review.measurements.margin_utilization == pytest.approx(0.0728)

    assert tuple(item.limit_id for item in review.limit_checks) == (
        "per_contract",
        "per_commodity",
        "per_sector",
        "per_exchange",
        "per_strategy",
        "per_account",
        "gross_leverage",
        "net_leverage",
        "margin_utilization",
    )
    assert all(item.status is LimitStatus.PASS for item in review.limit_checks)
    assert tuple(item.kind for item in review.stress_checks) == tuple(sorted(ScenarioKind, key=lambda item: item.value))
    assert all(item.status is LimitStatus.PASS for item in review.stress_checks)
    assert evidence.approved_target is not None
    assert evidence.approved_target.risk_evidence_hash == review.review_hash
    assert evidence.approved_target.approval_id == fixture.attestation.approval_id
    assert evidence.rejection_reasons == ()
    assert evidence.eligible_for_execution is False
    assert evidence.eligible_for_broker_order is False


def test_reordered_typed_inputs_replay_to_the_same_review_and_approval_identity() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    reversed_policy = replace(
        fixture.policy.stress_policy,
        scenario_limits=tuple(reversed(fixture.policy.stress_policy.scenario_limits)),
    )
    reordered_request = replace(
        fixture.review_request,
        instrument_snapshots=tuple(reversed(fixture.instrument_snapshots)),
        policy=replace(fixture.policy, stress_policy=reversed_policy),
    )
    gate = PortfolioRiskApprovalGate()

    replayed_review = gate.review(reordered_request)

    assert reordered_request == fixture.review_request
    assert replayed_review == fixture.review
    assert replayed_review.review_hash == fixture.review.review_hash


def test_market_input_mutation_changes_derived_risk_identity_without_accepting_precomputed_metrics() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    changed_snapshot = replace(fixture.instrument_snapshots[0], margin_fraction=0.2)
    changed_request = replace(
        fixture.review_request,
        instrument_snapshots=(changed_snapshot, *fixture.instrument_snapshots[1:]),
    )

    changed_review = PortfolioRiskApprovalGate().review(changed_request)

    assert changed_review.review_hash != fixture.review.review_hash
    assert changed_review.exposure.margin_required != fixture.review.exposure.margin_required
    assert changed_review.measurements.margin_utilization != fixture.review.measurements.margin_utilization
    request_fields = {item.name for item in fields(type(fixture.review_request))}
    assert not request_fields.intersection(
        {
            "approved_target",
            "exposure",
            "exposure_snapshot",
            "limit_checks",
            "measurements",
            "portfolio_target",
            "risk_evidence_hash",
            "stress_checks",
            "stress_results",
        }
    )


def test_stress_results_cover_every_kind_and_bind_loss_and_margin_dimensions() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    by_kind = {item.kind: item for item in fixture.review.stress_checks}

    assert set(by_kind) == set(ScenarioKind)
    assert by_kind[ScenarioKind.MARGIN_INCREASE].stressed_margin == pytest.approx(40_040.0)
    assert by_kind[ScenarioKind.MARGIN_INCREASE].margin_utilization == pytest.approx(0.08008)
    assert by_kind[ScenarioKind.GAP].stressed_loss == pytest.approx(36_000.0)
    assert by_kind[ScenarioKind.GAP].loss_fraction == pytest.approx(0.036)
    assert all(item.loss_status is LimitStatus.PASS for item in by_kind.values())
    assert all(item.margin_status is LimitStatus.PASS for item in by_kind.values())
