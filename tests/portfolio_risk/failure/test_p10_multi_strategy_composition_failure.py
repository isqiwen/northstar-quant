"""Failure-closed P10-WP04 canonical composition coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from northstar_quant.portfolio_risk.allocation import (
    AllocationResult,
    StrategyAllocation,
    StrategyAllocationInput,
)
from northstar_quant.portfolio_risk.portfolio import (
    CanonicalPortfolioComposer,
    PortfolioCompositionError,
    PortfolioCompositionEvidence,
    PortfolioCompositionRequest,
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
)
from tests.helpers.canonical_multi_strategy_portfolio import (
    build_canonical_two_strategy_fixture,
)


def _request_with_inputs(*inputs: StrategyAllocationInput) -> PortfolioCompositionRequest:
    fixture = build_canonical_two_strategy_fixture()
    return replace(fixture.request, allocation_inputs=inputs)


def test_rejects_single_source_duplicate_source_identity_and_tampered_snapshot() -> None:
    fixture = build_canonical_two_strategy_fixture()
    with pytest.raises(PortfolioCompositionError, match="at least two sources"):
        _request_with_inputs(fixture.allocation_inputs[0])

    duplicate_source = replace(
        fixture.strategy_targets[1],
        source_strategy_id=fixture.strategy_targets[0].source_strategy_id,
    )
    with pytest.raises(PortfolioCompositionError, match="duplicate source strategy"):
        _request_with_inputs(
            fixture.allocation_inputs[0],
            replace(fixture.allocation_inputs[1], strategy_target=duplicate_source),
        )

    tampered = fixture.strategy_targets[0]
    object.__setattr__(tampered, "target_hash", "0" * 64)
    with pytest.raises(PortfolioCompositionError, match="strategy_target replay mismatch"):
        _request_with_inputs(
            replace(fixture.allocation_inputs[0], strategy_target=tampered),
            fixture.allocation_inputs[1],
        )


def test_rejects_source_time_window_mismatch_and_future_effectiveness() -> None:
    fixture = build_canonical_two_strategy_fixture()
    with pytest.raises(PortfolioCompositionError, match="portfolio expiry exceeds"):
        replace(
            fixture.request,
            expires_at=fixture.strategy_targets[0].expires_at + timedelta(seconds=1),
        )

    source = fixture.strategy_targets[0]
    source_generated_at = fixture.request.generated_at - timedelta(minutes=1)
    future_source = StrategyTarget(
        target_id="future-effective-source",
        source_strategy_id="futures.future-effective",
        source_strategy_version="1.0.0",
        generated_at=source_generated_at,
        effective_at=fixture.request.generated_at + timedelta(seconds=1),
        expires_at=source.expires_at,
        positions=(TargetPosition("SHFE.RB2610", 0.1),),
        activation=StrategyTargetActivationRef(
            activation_id="future-effective-activation",
            activation_hash="1" * 64,
            approved_at=source_generated_at,
        ),
    )
    with pytest.raises(PortfolioCompositionError, match="not effective at portfolio generation"):
        _request_with_inputs(
            replace(fixture.allocation_inputs[0], strategy_target=future_source),
            fixture.allocation_inputs[1],
        )


def test_rejects_precomputed_partial_allocation_and_non_request_input() -> None:
    fixture = build_canonical_two_strategy_fixture()
    partial = AllocationResult(
        allocations=(
            StrategyAllocation(
                strategy_target_hash=fixture.strategy_targets[0].target_hash,
                allocation=0.4,
                volatility_scale=1.0,
            ),
        ),
        unallocated_cash=0.6,
    )
    with pytest.raises(PortfolioCompositionError, match="allocation result replay mismatch"):
        PortfolioCompositionEvidence(
            request=fixture.request,
            portfolio_target=fixture.evidence.portfolio_target,
            allocation_result=partial,
            contributions=fixture.evidence.contributions,
            composition_hash=fixture.evidence.composition_hash,
        )
    with pytest.raises(PortfolioCompositionError, match="request must be an exact"):
        CanonicalPortfolioComposer().compose(fixture.evidence.portfolio_target)  # type: ignore[arg-type]
