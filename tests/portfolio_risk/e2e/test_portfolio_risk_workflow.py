"""P3 target→allocation→exposure→risk has no execution escape hatch."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from northstar_quant.portfolio_risk.allocation import AllocationPolicy, StrategyAllocationInput, allocate
from northstar_quant.portfolio_risk.exposure import Direction, ExposurePosition, calculate_exposure
from northstar_quant.portfolio_risk.limits import LimitStatus, RiskLimitSet, RiskMeasurements, evaluate_limits
from northstar_quant.portfolio_risk.portfolio import (
    PortfolioRiskApprovalGate,
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
)
from tests.helpers.approved_portfolio_target import build_approved_portfolio_target_fixture
from tests.helpers.canonical_portfolio_risk import build_approval_request


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _strategy_target() -> StrategyTarget:
    now = datetime(2026, 8, 21, 9, tzinfo=UTC)
    return StrategyTarget(
        "trend-target",
        "futures.trend",
        "1.0",
        now,
        now + timedelta(minutes=1),
        now + timedelta(hours=1),
        (TargetPosition("SHFE.RB2610", 0.2),),
        StrategyTargetActivationRef("manual-activation", _hash("manual-activation"), now),
    )


def test_portfolio_risk_e2e_approves_only_when_every_limit_passes():
    strategy_target = _strategy_target()
    allocation = allocate(policy=AllocationPolicy(0.1, 0.1), inputs=(StrategyAllocationInput(strategy_target, 0.5, 0.1, 1, 0.5),))
    exposure = calculate_exposure(positions=(ExposurePosition("SHFE.RB2610", "RB", "ferrous", "SHFE", "china_steel", Direction.LONG, 50, 5),))
    checks = evaluate_limits(limits=RiskLimitSet(1, 1, 1, 1, 1, 1, 2, 1, 0.8), measurements=RiskMeasurements(0.2, 0.2, 0.2, 0.2, allocation.allocations[0].allocation, 0.2, 0.5, 0.5, exposure.margin_required / 100))
    assert all(check.status is LimitStatus.PASS for check in checks)
    fixture = build_approved_portfolio_target_fixture()
    approved = fixture.approval_evidence.approved_target
    assert approved is not None
    assert fixture.review.eligible_for_approval is True
    assert approved.risk_evidence_hash == fixture.review.review_hash
    assert approved.eligible_for_broker_order is False


def test_blocked_portfolio_risk_e2e_has_no_approved_target():
    fixture = build_approved_portfolio_target_fixture()
    review_request = fixture.approval_evidence.approval_request.review_request
    blocked_request = replace(
        review_request,
        account_snapshot=replace(review_request.account_snapshot, equity=None),
    )
    evidence = PortfolioRiskApprovalGate().evaluate(build_approval_request(blocked_request))
    assert evidence.approved_target is None
    assert "REVIEW_UNKNOWN" in evidence.rejection_reasons
