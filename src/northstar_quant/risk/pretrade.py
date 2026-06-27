"""交易前风控。"""

from __future__ import annotations

import math
from collections.abc import Mapping

from northstar_quant.execution.models import OrderRequest
from northstar_quant.execution.quantity import resolve_qty_step
from northstar_quant.risk.models import OrderRiskContext, RiskLimits

_FINAL_OPEN_ORDER_STATUSES = {
    "filled",
    "cancelled",
    "canceled",
    "apicancelled",
    "inactive",
    "rejected",
}


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


def _coerce_finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _coerce_positive_float(value: object) -> float | None:
    parsed = _coerce_finite_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _is_working_open_order(row: Mapping[str, object]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    return status not in _FINAL_OPEN_ORDER_STATUSES


def _remaining_open_order_qty(row: Mapping[str, object]) -> float | None:
    for key in ("remaining_qty", "remaining", "leaves_qty"):
        remaining_qty = _coerce_positive_float(row.get(key))
        if remaining_qty is not None:
            return remaining_qty

    total_qty = _coerce_positive_float(row.get("qty") or row.get("total_qty") or row.get("totalQuantity"))
    if total_qty is None:
        return None
    filled_qty = _coerce_finite_float(row.get("filled_qty") or row.get("filled") or 0.0) or 0.0
    remaining_qty = max(total_qty - max(filled_qty, 0.0), 0.0)
    return remaining_qty if remaining_qty > 1e-8 else None


def _open_order_notional(row: Mapping[str, object]) -> float | None:
    for key in ("remaining_notional", "remaining_trade_value", "planned_trade_value", "notional"):
        notional = _coerce_positive_float(row.get(key))
        if notional is not None:
            return notional
    return None


def _open_order_price_basis(
    row: Mapping[str, object],
    reference_prices: Mapping[str, float] | None,
) -> float | None:
    for key in (
        "reference_price",
        "limit_price",
        "price",
        "avg_fill_price",
        "market_price",
        "last",
        "close",
    ):
        price = _coerce_positive_float(row.get(key))
        if price is not None:
            return price

    symbol = _normalize_symbol(str(row.get("symbol") or ""))
    if not symbol or reference_prices is None:
        return None
    return _coerce_positive_float(reference_prices.get(symbol))


def reserve_open_orders_in_context(
    context: OrderRiskContext | None,
    open_orders: list[dict],
    reference_prices: Mapping[str, float] | None = None,
) -> None:
    """把券商未完成订单计入动态风控上下文。

    未完成买单会占用可用资金；未完成卖单会占用可卖持仓。
    如果方向、数量或买单估值价格无法解析，则标记为 unresolved，
    后续下单会 fail-closed。
    """

    if context is None:
        return

    normalized_reference_prices = (
        {_normalize_symbol(str(symbol)): float(price) for symbol, price in reference_prices.items()}
        if reference_prices
        else None
    )

    for row in open_orders:
        if not _is_working_open_order(row):
            continue

        side = str(row.get("side") or row.get("action") or "").strip().upper()
        symbol = _normalize_symbol(str(row.get("symbol") or ""))
        remaining_qty = _remaining_open_order_qty(row)
        if side not in {"BUY", "SELL"} or not symbol or remaining_qty is None:
            context.unresolved_open_order_count += 1
            continue

        if side == "BUY":
            notional = _open_order_notional(row)
            if notional is None:
                price_basis = _open_order_price_basis(row, normalized_reference_prices)
                if price_basis is None:
                    context.unresolved_open_order_count += 1
                    continue
                notional = remaining_qty * price_basis
            context.reserved_buy_notional += notional
        elif side == "SELL":
            context.reserved_sell_qty_by_symbol[symbol] = (
                float(context.reserved_sell_qty_by_symbol.get(symbol, 0.0)) + remaining_qty
            )


def _validate_account_context(
    order: OrderRequest,
    qty: float,
    context: OrderRiskContext | None,
) -> None:
    if context is None:
        return

    if context.unresolved_open_order_count > 0:
        raise ValueError("账户存在无法解析的未完成订单")

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
