"""P10-WP05 pure-P3 end-to-end portfolio-risk approval boundary."""

from __future__ import annotations

from northstar_quant.portfolio_risk.portfolio import (
    ApprovedPortfolioTarget,
    PortfolioRiskApprovalEvidence,
    PortfolioRiskApprovalGate,
)
from tests.helpers.canonical_portfolio_risk import (
    build_canonical_portfolio_risk_fixture,
)


def test_two_activated_strategies_compose_review_and_require_named_p3_attestation() -> None:
    fixture = build_canonical_portfolio_risk_fixture()
    gate = PortfolioRiskApprovalGate()

    replayed = gate.evaluate(fixture.approval_request)

    assert isinstance(replayed, PortfolioRiskApprovalEvidence)
    assert replayed == fixture.approval_evidence
    assert replayed.composition_evidence_hash == fixture.composition_fixture.evidence.evidence_hash
    assert replayed.portfolio_target == fixture.composition_fixture.evidence.portfolio_target
    assert isinstance(replayed.approved_target, ApprovedPortfolioTarget)
    assert replayed.approved_target.approval_hash == fixture.approval_evidence.approved_target.approval_hash
    assert replayed.approved_target.risk_evidence_hash == replayed.review.review_hash
    assert replayed.approved_target.eligible_for_execution is False
    assert replayed.approved_target.eligible_for_broker_order is False
    assert replayed.eligible_for_execution is False
    assert replayed.eligible_for_broker_order is False
    assert not hasattr(replayed, "execution_plan")
    assert not hasattr(replayed, "broker_order")
    assert not hasattr(replayed, "durable_intent")
