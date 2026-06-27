"""交易前风控。"""

from __future__ import annotations

import math

from northstar_quant.execution.models import OrderRequest
from northstar_quant.execution.quantity import resolve_qty_step
from northstar_quant.risk.models import RiskLimits


def _require_finite_positive(value: float, message: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(message)
    return parsed


def _resolve_order_notional(order: OrderRequest) -> float | None:
    """解析订单金额，用于单笔 notional 风控。"""

    if order.planned_trade_value is not None:
        planned_trade_value = float(order.planned_trade_value)
        if not math.isfinite(planned_trade_value):
            return None
        return abs(planned_trade_value)

    price_basis = order.reference_price
    if price_basis is None:
        price_basis = order.limit_price

    if price_basis is None:
        return None

    price = _require_finite_positive(float(price_basis), "订单金额风控价格基准必须大于 0")
    return abs(float(order.qty)) * price


def _validate_qty_step(qty: float, step: float | None) -> None:
    if step is None:
        return
    parsed_step = float(step)
    if parsed_step <= 0:
        return
    units = qty / parsed_step
    if not math.isclose(units, round(units), rel_tol=1e-9, abs_tol=1e-8):
        raise ValueError("订单数量不符合交易单位步长")


def validate_order(order: OrderRequest, limits: RiskLimits) -> None:
    """验证单笔订单是否满足交易前约束。"""

    qty = _require_finite_positive(float(order.qty), "订单数量必须大于 0")

    if qty > limits.max_order_qty:
        raise ValueError("订单数量超过风控上限")

    qty_step = resolve_qty_step(
        order.side,
        order_qty_step=limits.order_qty_step,
        buy_qty_step=limits.buy_qty_step,
        sell_qty_step=limits.sell_qty_step,
    )
    _validate_qty_step(qty, qty_step)

    if order.target_weight is not None and abs(order.target_weight) > limits.max_single_weight:
        raise ValueError("目标权重超过单标的权重上限")

    if order.limit_price is not None:
        _require_finite_positive(float(order.limit_price), "限价必须大于 0")

    if limits.min_order_notional is not None and limits.min_order_notional > 0:
        order_notional = _resolve_order_notional(order)
        if order_notional is None:
            raise ValueError("订单金额风控缺少价格基准")
        if order_notional < limits.min_order_notional:
            raise ValueError("订单金额低于风控下限")

    if limits.max_order_notional is not None:
        order_notional = _resolve_order_notional(order)
        if order_notional is None:
            raise ValueError("订单金额风控缺少价格基准")
        if order_notional > limits.max_order_notional:
            raise ValueError("订单金额超过风控上限")
