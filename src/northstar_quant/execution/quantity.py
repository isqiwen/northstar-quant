"""Order quantity helpers."""

from __future__ import annotations

import math


def resolve_qty_step(
    side: str,
    *,
    order_qty_step: float | None = None,
    buy_qty_step: float | None = None,
    sell_qty_step: float | None = None,
) -> float | None:
    """Resolve the quantity step for the order side."""

    normalized_side = side.strip().upper()
    if normalized_side == "BUY" and buy_qty_step is not None:
        return buy_qty_step
    if normalized_side == "SELL" and sell_qty_step is not None:
        return sell_qty_step
    return order_qty_step


def round_qty_down_to_step(qty: float, step: float | None) -> float:
    """Round quantity down to the nearest valid positive step."""

    parsed_qty = float(qty)
    if step is None:
        return parsed_qty

    parsed_step = float(step)
    if parsed_step <= 0:
        return parsed_qty

    rounded = math.floor((parsed_qty / parsed_step) + 1e-12) * parsed_step
    return round(rounded, 10)


def round_order_qty_down(
    qty: float,
    side: str,
    *,
    order_qty_step: float | None = None,
    buy_qty_step: float | None = None,
    sell_qty_step: float | None = None,
) -> float:
    """Round an order quantity down according to side-aware lot rules."""

    step = resolve_qty_step(
        side,
        order_qty_step=order_qty_step,
        buy_qty_step=buy_qty_step,
        sell_qty_step=sell_qty_step,
    )
    return round_qty_down_to_step(qty, step)

