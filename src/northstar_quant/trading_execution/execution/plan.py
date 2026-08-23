"""Typed execution-plan boundary; plans are never broker orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import polars as pl

from northstar_quant.foundation.common.enums import StrategyOutputType
from northstar_quant.foundation.common.time import ensure_utc
from northstar_quant.foundation.config.trading_profile import TradingProfile
from northstar_quant.portfolio_risk.portfolio import ApprovedPortfolioTarget
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    FuturesExecutionRule,
    RebalanceOrderPlan,
)


class ExecutionPlanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """An auditable plan derived from an approved target, not a submit payload."""

    plan_id: str
    approved_target: ApprovedPortfolioTarget
    account_snapshot: BrokerStateSnapshot
    market_snapshot_at: datetime
    contract_rules: dict[str, FuturesExecutionRule]
    orders: tuple[RebalanceOrderPlan, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ExecutionPlanError("plan_id is required")
        if not isinstance(self.approved_target, ApprovedPortfolioTarget):
            raise ExecutionPlanError("approved_target is required")
        if self.account_snapshot.asof is None:
            raise ExecutionPlanError("account snapshot timestamp is required")
        if not isinstance(self.market_snapshot_at, datetime):
            raise ExecutionPlanError("market snapshot timestamp is required")
        if not isinstance(self.created_at, datetime):
            raise ExecutionPlanError("created_at is required")
        if not self.contract_rules:
            raise ExecutionPlanError("contract rules are required")
        if not self.orders:
            raise ExecutionPlanError("execution plan cannot be empty")
        object.__setattr__(self, "plan_id", self.plan_id.strip())
        object.__setattr__(self, "market_snapshot_at", ensure_utc(self.market_snapshot_at))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "contract_rules", dict(self.contract_rules))

    @property
    def eligible_for_broker_order(self) -> bool:
        """Pre-trade approval must create broker submit payloads separately."""

        return False


def build_approved_execution_plan(
    *,
    plan_id: str,
    approved_target: ApprovedPortfolioTarget,
    profile: TradingProfile,
    account_snapshot: BrokerStateSnapshot,
    latest_prices: dict[str, float],
    market_snapshot_at: datetime,
    created_at: datetime,
    broker_name: str,
    futures_rules: dict[str, FuturesExecutionRule],
    equity: float | None = None,
) -> ExecutionPlan:
    """Bind the existing planner to a risk-approved target and immutable inputs."""

    if not isinstance(approved_target, ApprovedPortfolioTarget):
        raise ExecutionPlanError("approved_target is required")
    planning_at = ensure_utc(created_at)
    target = approved_target.portfolio_target
    if not target.effective_at <= planning_at < target.expires_at:
        raise ExecutionPlanError("approved portfolio target is not effective")
    if account_snapshot.asof is None:
        raise ExecutionPlanError("account snapshot timestamp is required")
    output = pl.DataFrame(
        {
            "symbol": [item.instrument_id.upper() for item in target.positions],
            "target_weight": [item.target_weight for item in target.positions],
        }
    )
    from northstar_quant.trading_execution.execution.registry import build_execution_plan

    orders = build_execution_plan(
        profile,
        output,
        StrategyOutputType.TARGET_WEIGHT,
        account_snapshot,
        latest_prices,
        equity=equity,
        broker_name=broker_name,
        futures_rules=futures_rules,
    )
    return ExecutionPlan(
        plan_id=plan_id,
        approved_target=approved_target,
        account_snapshot=account_snapshot,
        market_snapshot_at=market_snapshot_at,
        contract_rules=futures_rules,
        orders=tuple(orders),
        created_at=planning_at,
    )


__all__ = ["ExecutionPlan", "ExecutionPlanError", "build_approved_execution_plan"]
