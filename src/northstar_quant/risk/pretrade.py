"""交易前风控。"""

from __future__ import annotations

import math

from northstar_quant.execution.models import OrderRequest
from northstar_quant.execution.quantity import resolve_qty_step
from northstar_quant.risk.models import OrderRiskContext, RiskLimits


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


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _validate_account_context(
    order: OrderRequest,
    qty: float,
    context: OrderRiskContext | None,
) -> None:
    if context is None:
        return

    side = order.side.strip().upper()
    symbol = _normalize_symbol(order.symbol)
    if side == "BUY" and context.available_cash is not None:
        order_notional = _resolve_order_notional(order)
        if order_notional is None:
            raise ValueError("买入可用资金检查缺少价格基准")
        available_cash = float(context.available_cash) - float(context.reserved_buy_notional)
        if order_notional > available_cash + 1e-8:
            raise ValueError("买入订单金额超过可用资金")

    if side == "SELL":
        position_qty = float(context.position_qty_by_symbol.get(symbol, 0.0))
        reserved_qty = float(context.reserved_sell_qty_by_symbol.get(symbol, 0.0))
        available_qty = position_qty - reserved_qty
        if qty > available_qty + 1e-8:
            raise ValueError("卖出订单数量超过可用持仓")


def reserve_order_context(context: OrderRiskContext | None, order: OrderRequest) -> None:
    """Reserve account capacity after an accepted order."""

    if context is None:
        return

    side = order.side.strip().upper()
    symbol = _normalize_symbol(order.symbol)
    if side == "BUY":
        order_notional = _resolve_order_notional(order)
        if order_notional is not None:
            context.reserved_buy_notional += order_notional
    elif side == "SELL":
        context.reserved_sell_qty_by_symbol[symbol] = (
            float(context.reserved_sell_qty_by_symbol.get(symbol, 0.0)) + float(order.qty)
        )


def release_order_context(context: OrderRiskContext | None, order: OrderRequest) -> None:
    """Release account capacity when an accepted order is canceled."""

    if context is None:
        return

    side = order.side.strip().upper()
    symbol = _normalize_symbol(order.symbol)
    if side == "BUY":
        order_notional = _resolve_order_notional(order)
        if order_notional is not None:
            context.reserved_buy_notional = max(0.0, context.reserved_buy_notional - order_notional)
    elif side == "SELL":
        reserved_qty = float(context.reserved_sell_qty_by_symbol.get(symbol, 0.0))
        remaining_qty = max(0.0, reserved_qty - float(order.qty))
        if remaining_qty:
            context.reserved_sell_qty_by_symbol[symbol] = remaining_qty
        else:
            context.reserved_sell_qty_by_symbol.pop(symbol, None)


def validate_order(
    order: OrderRequest,
    limits: RiskLimits,
    context: OrderRiskContext | None = None,
) -> None:
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

    _validate_account_context(order, qty, context)
