from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.portfolio_risk.allocation import AllocationError, AllocationPolicy, StrategyAllocationInput, allocate
from northstar_quant.portfolio_risk.portfolio import (
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
)


def _activation(name: str, approved_at: datetime) -> StrategyTargetActivationRef:
    return StrategyTargetActivationRef(
        activation_id=f"activation.{name}",
        activation_hash=sha256(f"activation:{name}".encode()).hexdigest(),
        approved_at=approved_at,
    )


def _target(name: str) -> StrategyTarget:
    now = datetime(2026, 8, 21, 9, tzinfo=UTC)
    return StrategyTarget(
        name,
        f"strategy.{name}",
        "1.0",
        now,
        now + timedelta(minutes=1),
        now + timedelta(hours=1),
        (TargetPosition("SHFE.RB2610", 0.2),),
        _activation(name, now),
    )


def test_allocation_uses_fixed_risk_volatility_caps_and_cash_reserve() -> None:
    result = allocate(
        policy=AllocationPolicy(cash_reserve=0.1, target_volatility=0.1),
        inputs=(
            StrategyAllocationInput(_target("one"), 0.7, 0.2, 1.0, 0.4),
            StrategyAllocationInput(_target("two"), 0.6, 0.1, 1.0, 0.5),
        ),
    )
    assert sum(item.allocation for item in result.allocations) == pytest.approx(0.675)
    assert result.unallocated_cash == pytest.approx(0.325)
    assert result.allocation_hash


def test_allocation_fails_closed_when_risk_budget_is_unknown_or_zero() -> None:
    with pytest.raises(AllocationError, match="risk_budget total"):
        allocate(
            policy=AllocationPolicy(cash_reserve=0.1, target_volatility=0.1),
            inputs=(StrategyAllocationInput(_target("one"), 0.5, 0.1, 0.0, 0.5),),
        )
