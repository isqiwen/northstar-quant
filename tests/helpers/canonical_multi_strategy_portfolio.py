"""Reusable P10-WP04 fixture for structured multi-strategy composition tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from northstar_quant.portfolio_risk.allocation import (
    AllocationPolicy,
    StrategyAllocationInput,
)
from northstar_quant.portfolio_risk.portfolio import (
    CanonicalPortfolioComposer,
    PortfolioCompositionEvidence,
    PortfolioCompositionRequest,
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
)


@dataclass(frozen=True, slots=True)
class CanonicalCompositionFixture:
    """Two independent, activated targets plus their canonical composition."""

    strategy_targets: tuple[StrategyTarget, StrategyTarget]
    allocation_policy: AllocationPolicy
    allocation_inputs: tuple[StrategyAllocationInput, StrategyAllocationInput]
    request: PortfolioCompositionRequest
    evidence: PortfolioCompositionEvidence


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _target(
    *,
    target_id: str,
    strategy_id: str,
    activation_id: str,
    positions: tuple[TargetPosition, ...],
) -> StrategyTarget:
    generated_at = datetime(2026, 8, 23, 9, tzinfo=UTC)
    return StrategyTarget(
        target_id=target_id,
        source_strategy_id=strategy_id,
        source_strategy_version="1.0.0",
        generated_at=generated_at,
        effective_at=generated_at + timedelta(minutes=1),
        expires_at=generated_at + timedelta(hours=2),
        positions=positions,
        activation=StrategyTargetActivationRef(
            activation_id=activation_id,
            activation_hash=_hash(f"activation:{activation_id}"),
            approved_at=generated_at,
        ),
    )


def build_canonical_two_strategy_fixture() -> CanonicalCompositionFixture:
    """Build a composition with residual cash that must never be rescaled away."""

    target_a = _target(
        target_id="p10-strategy-a-target",
        strategy_id="futures.alpha",
        activation_id="p10-alpha-activation",
        positions=(
            TargetPosition("SHFE.CU2610", 0.25),
            TargetPosition("SHFE.RB2610", 0.5),
        ),
    )
    target_b = _target(
        target_id="p10-strategy-b-target",
        strategy_id="futures.beta",
        activation_id="p10-beta-activation",
        positions=(
            TargetPosition("SHFE.AU2610", 0.4),
            TargetPosition("SHFE.RB2610", -0.2),
        ),
    )
    policy = AllocationPolicy(cash_reserve=0.1, target_volatility=0.1)
    inputs = (
        StrategyAllocationInput(
            strategy_target=target_a,
            fixed_budget=0.4,
            realized_volatility=0.1,
            risk_budget=1.0,
            max_allocation=0.4,
        ),
        StrategyAllocationInput(
            strategy_target=target_b,
            fixed_budget=0.3,
            realized_volatility=0.1,
            risk_budget=1.0,
            max_allocation=0.3,
        ),
    )
    generated_at = target_a.effective_at + timedelta(minutes=1)
    request = PortfolioCompositionRequest(
        target_id="p10-canonical-two-strategy-portfolio",
        generated_at=generated_at,
        effective_at=generated_at + timedelta(minutes=1),
        expires_at=generated_at + timedelta(hours=1),
        allocation_policy=policy,
        allocation_inputs=inputs,
    )
    evidence = CanonicalPortfolioComposer().compose(request)
    return CanonicalCompositionFixture(
        strategy_targets=(target_a, target_b),
        allocation_policy=policy,
        allocation_inputs=inputs,
        request=request,
        evidence=evidence,
    )


__all__ = ["CanonicalCompositionFixture", "build_canonical_two_strategy_fixture"]
