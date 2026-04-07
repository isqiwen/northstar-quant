"""Helpers for execution-intent order semantics."""

from __future__ import annotations

import math

from northstar_quant.common.enums import OrderSemantic


def resolve_execution_intent_qty(
    *,
    side: str,
    order_semantic: str,
    size_fraction: float,
    price: float,
    equity: float,
    current_qty: float,
) -> int:
    """Resolve the executable quantity for an execution intent."""

    if price <= 0 or size_fraction <= 0 or equity <= 0:
        return 0

    side = str(side).upper()
    semantic = OrderSemantic.parse(order_semantic)
    desired_entry_qty = math.floor(equity * size_fraction / price)

    if side == "BUY":
        reducible_qty = abs(min(float(current_qty), 0.0))
    else:
        reducible_qty = max(float(current_qty), 0.0)

    if semantic == OrderSemantic.ENTRY:
        return max(desired_entry_qty, 0)

    if semantic == OrderSemantic.EXIT:
        return math.floor(reducible_qty)

    if semantic == OrderSemantic.REDUCE:
        return min(math.floor(reducible_qty * size_fraction), math.floor(reducible_qty))

    if semantic == OrderSemantic.REVERSE:
        return math.floor(reducible_qty) + max(desired_entry_qty, 0)

    return 0
