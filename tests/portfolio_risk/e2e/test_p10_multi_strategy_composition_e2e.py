"""P10-WP04 end-to-end composition boundary, intentionally before approval."""

from __future__ import annotations

from northstar_quant.portfolio_risk.portfolio import PortfolioTarget
from tests.helpers.canonical_multi_strategy_portfolio import (
    build_canonical_two_strategy_fixture,
)


def test_two_activated_strategy_targets_replay_to_one_non_executable_portfolio_target() -> None:
    fixture = build_canonical_two_strategy_fixture()
    evidence = fixture.evidence

    assert isinstance(evidence.portfolio_target, PortfolioTarget)
    assert len(evidence.request.allocation_inputs) == 2
    assert len(evidence.allocation_result.allocations) == 2
    assert len(evidence.contributions) == 2
    assert evidence.portfolio_target.composition_hash == evidence.composition_hash
    assert evidence.eligible_for_portfolio_approval is False
    assert evidence.eligible_for_execution is False
    assert evidence.eligible_for_broker_order is False
    assert not hasattr(evidence, "approval_hash")
    assert not hasattr(evidence, "plan_hash")
    assert not hasattr(evidence, "order_hash")
