"""P3-WP02 deterministic first-stage allocation models."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from northstar_quant.portfolio_risk.portfolio.targets import StrategyTarget


class AllocationError(ValueError):
    """Allocation inputs are incomplete or would silently add risk."""


def _number(value: object, field_name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AllocationError(f"{field_name} must be a finite number")
    normalized = float(value)
    if normalized < 0 or (positive and normalized <= 0):
        raise AllocationError(f"{field_name} must be {'positive' if positive else 'non-negative'}")
    return normalized


@dataclass(frozen=True, slots=True)
class AllocationPolicy:
    """Explicit fixed-budget, volatility, risk-budget and cash-reserve policy."""

    cash_reserve: float
    target_volatility: float

    def __post_init__(self) -> None:
        cash_reserve = _number(self.cash_reserve, "cash_reserve")
        if cash_reserve >= 1:
            raise AllocationError("cash_reserve must be below 1")
        object.__setattr__(self, "cash_reserve", cash_reserve)
        object.__setattr__(self, "target_volatility", _number(self.target_volatility, "target_volatility", positive=True))


@dataclass(frozen=True, slots=True)
class StrategyAllocationInput:
    strategy_target: StrategyTarget
    fixed_budget: float
    realized_volatility: float
    risk_budget: float
    max_allocation: float

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_target, StrategyTarget):
            raise AllocationError("strategy_target must be a StrategyTarget")
        fixed_budget = _number(self.fixed_budget, "fixed_budget")
        risk_budget = _number(self.risk_budget, "risk_budget")
        cap = _number(self.max_allocation, "max_allocation")
        if cap > 1:
            raise AllocationError("max_allocation cannot exceed 1")
        object.__setattr__(self, "fixed_budget", fixed_budget)
        object.__setattr__(self, "realized_volatility", _number(self.realized_volatility, "realized_volatility", positive=True))
        object.__setattr__(self, "risk_budget", risk_budget)
        object.__setattr__(self, "max_allocation", cap)


@dataclass(frozen=True, slots=True)
class StrategyAllocation:
    strategy_target_hash: str
    allocation: float
    volatility_scale: float


@dataclass(frozen=True, slots=True)
class AllocationResult:
    allocations: tuple[StrategyAllocation, ...]
    unallocated_cash: float
    allocation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.allocations, tuple) or not self.allocations:
            raise AllocationError("allocations must be non-empty")
        if any(not isinstance(item, StrategyAllocation) for item in self.allocations):
            raise AllocationError("allocations must contain StrategyAllocation")
        if len({item.strategy_target_hash for item in self.allocations}) != len(self.allocations):
            raise AllocationError("allocations cannot duplicate a strategy target")
        cash = _number(self.unallocated_cash, "unallocated_cash")
        if cash > 1:
            raise AllocationError("unallocated_cash cannot exceed 1")
        allocations = tuple(sorted(self.allocations, key=lambda item: item.strategy_target_hash))
        if any(item.allocation < 0 or item.volatility_scale <= 0 for item in allocations):
            raise AllocationError("allocation must be non-negative and volatility_scale positive")
        if sum(item.allocation for item in allocations) + cash > 1 + 1e-12:
            raise AllocationError("allocation plus cash cannot exceed 1")
        payload = {
            "format": "northstar.allocation-result.v1",
            "allocations": [
                {"strategy_target_hash": item.strategy_target_hash, "allocation": item.allocation, "volatility_scale": item.volatility_scale}
                for item in allocations
            ],
            "unallocated_cash": cash,
        }
        import hashlib
        import json
        object.__setattr__(self, "allocations", allocations)
        object.__setattr__(self, "unallocated_cash", cash)
        object.__setattr__(self, "allocation_hash", hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest())


def allocate(*, policy: AllocationPolicy, inputs: tuple[StrategyAllocationInput, ...]) -> AllocationResult:
    """Allocate conservatively; caps and reserve leave excess as cash, never rescale it away."""

    if not isinstance(policy, AllocationPolicy):
        raise AllocationError("policy must be an AllocationPolicy")
    if not isinstance(inputs, tuple) or not inputs or not all(isinstance(item, StrategyAllocationInput) for item in inputs):
        raise AllocationError("inputs must be a non-empty StrategyAllocationInput tuple")
    if len({item.strategy_target.target_hash for item in inputs}) != len(inputs):
        raise AllocationError("inputs cannot duplicate a strategy target")
    remaining = 1 - policy.cash_reserve
    total_risk_budget = sum(item.risk_budget for item in inputs)
    if total_risk_budget <= 0:
        raise AllocationError("risk_budget total must be positive")
    allocations: list[StrategyAllocation] = []
    for item in sorted(inputs, key=lambda value: value.strategy_target.target_hash):
        volatility_scale = min(1.0, policy.target_volatility / item.realized_volatility)
        risk_weight = item.risk_budget / total_risk_budget
        requested = min(item.fixed_budget, remaining * risk_weight) * volatility_scale
        allocation = min(requested, item.max_allocation)
        allocations.append(StrategyAllocation(item.strategy_target.target_hash, allocation, volatility_scale))
    allocated = sum(item.allocation for item in allocations)
    return AllocationResult(allocations=tuple(allocations), unallocated_cash=1 - allocated)


__all__ = ["AllocationError", "AllocationPolicy", "AllocationResult", "StrategyAllocation", "StrategyAllocationInput", "allocate"]
