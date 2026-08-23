"""Unit coverage for P10-WP04 canonical multi-strategy composition."""

from __future__ import annotations

from dataclasses import replace

import pytest

from northstar_quant.portfolio_risk.allocation import (
    AllocationPolicy,
    StrategyAllocationInput,
)
from northstar_quant.portfolio_risk.portfolio import (
    CanonicalPortfolioComposer,
    PortfolioCompositionRequest,
    TargetPosition,
)
from tests.helpers.canonical_multi_strategy_portfolio import (
    build_canonical_two_strategy_fixture,
)


def test_canonical_composition_is_deterministic_and_preserves_cash_and_lineage() -> None:
    fixture = build_canonical_two_strategy_fixture()
    evidence = fixture.evidence
    target = evidence.portfolio_target

    assert target.source_strategy_target_hashes == tuple(
        sorted(item.target_hash for item in fixture.strategy_targets)
    )
    assert [(item.instrument_id, item.target_weight) for item in target.positions] == [
        ("SHFE.AU2610", pytest.approx(0.12)),
        ("SHFE.CU2610", pytest.approx(0.10)),
        ("SHFE.RB2610", pytest.approx(0.14)),
    ]
    assert evidence.allocation_result.unallocated_cash == pytest.approx(0.3)
    assert sum(abs(item.target_weight) for item in target.positions) == pytest.approx(0.36)
    assert evidence.composition_hash == target.composition_hash
    assert target.as_mapping()["format"] == "northstar.portfolio-target.v2"
    assert target.as_mapping()["composition_hash"] == evidence.composition_hash
    assert [item.strategy_target_hash for item in evidence.contributions] == list(
        target.source_strategy_target_hashes
    )
    assert evidence.eligible_for_portfolio_approval is False
    assert evidence.eligible_for_execution is False
    assert evidence.eligible_for_broker_order is False

    permuted = replace(
        fixture.request,
        allocation_inputs=tuple(reversed(fixture.request.allocation_inputs)),
    )
    replay = CanonicalPortfolioComposer().compose(permuted)
    assert replay == evidence
    assert replay.as_mapping() == evidence.as_mapping()


def test_allocation_provenance_changes_target_identity_even_when_netted_positions_match() -> None:
    fixture = build_canonical_two_strategy_fixture()
    source_a = replace(
        fixture.strategy_targets[0],
        positions=(TargetPosition("SHFE.RB2610", 0.5),),
    )
    source_b = replace(
        fixture.strategy_targets[1],
        positions=(TargetPosition("SHFE.RB2610", 0.5),),
    )
    policy = AllocationPolicy(cash_reserve=0.1, target_volatility=0.1)

    first = PortfolioCompositionRequest(
        target_id="same-net-target",
        generated_at=fixture.request.generated_at,
        effective_at=fixture.request.effective_at,
        expires_at=fixture.request.expires_at,
        allocation_policy=policy,
        allocation_inputs=(
            StrategyAllocationInput(source_a, 0.4, 0.1, 1.0, 0.4),
            StrategyAllocationInput(source_b, 0.3, 0.1, 1.0, 0.3),
        ),
    )
    second = replace(
        first,
        allocation_inputs=(
            StrategyAllocationInput(source_a, 0.3, 0.1, 1.0, 0.3),
            StrategyAllocationInput(source_b, 0.4, 0.1, 1.0, 0.4),
        ),
    )
    composer = CanonicalPortfolioComposer()
    first_evidence = composer.compose(first)
    second_evidence = composer.compose(second)

    assert first_evidence.portfolio_target.positions == second_evidence.portfolio_target.positions
    assert first_evidence.portfolio_target.positions == (TargetPosition("SHFE.RB2610", 0.35),)
    assert first_evidence.composition_hash != second_evidence.composition_hash
    assert first_evidence.portfolio_target.target_hash != second_evidence.portfolio_target.target_hash


def test_cancellation_is_retained_as_explicit_zero_position_not_silently_dropped() -> None:
    fixture = build_canonical_two_strategy_fixture()
    source_a = replace(
        fixture.strategy_targets[0],
        positions=(TargetPosition("SHFE.RB2610", 0.5),),
    )
    source_b = replace(
        fixture.strategy_targets[1],
        positions=(TargetPosition("SHFE.RB2610", -0.5),),
    )
    request = PortfolioCompositionRequest(
        target_id="explicit-net-zero-target",
        generated_at=fixture.request.generated_at,
        effective_at=fixture.request.effective_at,
        expires_at=fixture.request.expires_at,
        allocation_policy=AllocationPolicy(cash_reserve=0.0, target_volatility=0.1),
        allocation_inputs=(
            StrategyAllocationInput(source_a, 0.5, 0.1, 1.0, 0.5),
            StrategyAllocationInput(source_b, 0.5, 0.1, 1.0, 0.5),
        ),
    )

    evidence = CanonicalPortfolioComposer().compose(request)
    assert evidence.portfolio_target.positions == (TargetPosition("SHFE.RB2610", 0.0),)
    assert len(evidence.contributions) == 2
