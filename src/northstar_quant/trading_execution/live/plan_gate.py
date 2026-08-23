"""Final plan-bound pre-trade guard for broker submission."""

from __future__ import annotations

import math

from northstar_quant.trading_execution.execution.models import OrderRequest, RebalanceOrderPlan
from northstar_quant.trading_execution.execution.plan import ExecutionPlan
from northstar_quant.trading_execution.live.preflight import PreflightResult


class PlanPreTradeGate:
    """Allow each approved plan item exactly once after a passing preflight."""

    def __init__(self, plan: ExecutionPlan, preflight: PreflightResult) -> None:
        self.plan = plan
        self.preflight = preflight
        self._consumed_indices: set[int] = set()

    def __call__(self, order: OrderRequest) -> None:
        if not self.preflight.can_trade:
            raise PermissionError("PREFLIGHT_BLOCKED: pre-trade checks did not pass")
        if order.plan_id != self.plan.plan_id:
            raise PermissionError("EXECUTION_PLAN_ID_MISMATCH: order is not bound to this plan")
        for index, item in enumerate(self.plan.orders):
            if index in self._consumed_indices or not _matches(item, order):
                continue
            self._consumed_indices.add(index)
            return
        raise PermissionError(
            "EXECUTION_PLAN_ORDER_MISMATCH: order is absent from the approved plan "
            "or was already submitted"
        )


def _matches(item: RebalanceOrderPlan, order: OrderRequest) -> bool:
    return (
        item.symbol == order.symbol
        and item.side == order.side
        and math.isclose(float(item.qty), float(order.qty), abs_tol=1e-8)
        and item.instrument_id == order.instrument_id
        and item.exchange_id == order.exchange_id
        and item.ctp_offset == order.ctp_offset
    )


__all__ = ["PlanPreTradeGate"]
