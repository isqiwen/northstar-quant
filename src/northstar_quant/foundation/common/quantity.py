"""订单数量与交易单位的纯函数。

该模块只包含无状态的数量规则，供执行计划和交易前风控共同复用。
把这些规则放在基础层，可以避免 ``risk`` 与 ``execution`` 形成反向依赖。
"""

from __future__ import annotations

import math


def resolve_qty_step(
    side: str,
    *,
    order_qty_step: float | None = None,
    buy_qty_step: float | None = None,
    sell_qty_step: float | None = None,
) -> float | None:
    """根据买卖方向解析订单数量步长。"""

    normalized_side = side.strip().upper()
    if normalized_side == "BUY" and buy_qty_step is not None:
        return buy_qty_step
    if normalized_side == "SELL" and sell_qty_step is not None:
        return sell_qty_step
    return order_qty_step


def round_qty_down_to_step(qty: float, step: float | None) -> float:
    """把数量向下取整到合法步长。"""

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
    """按买卖方向对应的交易单位向下取整订单数量。"""

    step = resolve_qty_step(
        side,
        order_qty_step=order_qty_step,
        buy_qty_step=buy_qty_step,
        sell_qty_step=sell_qty_step,
    )
    return round_qty_down_to_step(qty, step)
