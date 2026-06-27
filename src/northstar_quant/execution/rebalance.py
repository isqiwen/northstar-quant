"""条形频再平衡计划器。"""

from __future__ import annotations

import polars as pl

from northstar_quant.config.settings import get_settings
from northstar_quant.execution.models import PositionSnapshot, RebalanceOrderPlan


def build_rebalance_plan(
    targets: pl.DataFrame,
    positions: list[PositionSnapshot],
    latest_prices: dict[str, float],
    equity: float | None = None,
    *,
    rebalance_min_trade_value: float | None = None,
    rebalance_weight_tolerance: float = 0.0,
    long_only: bool = True,
) -> list[RebalanceOrderPlan]:
    """根据目标权重与真实持仓生成再平衡计划。

    逻辑说明：
    1. 先根据账户权益与目标权重计算目标持股数量
    2. 再与真实持仓比较，得到应该买多少 / 卖多少
    3. 过滤掉过小的交易，减少手续费与噪音

    注意：这里仍然是“计划”，不是直接下单。
    """

    settings = get_settings()
    equity = float(equity or settings.default_cash)
    min_trade_value = float(
        rebalance_min_trade_value
        if rebalance_min_trade_value is not None
        else settings.rebalance_min_trade_value
    )
    weight_tolerance = max(float(rebalance_weight_tolerance or 0.0), 0.0)
    pos_map = {p.symbol: p for p in positions}

    rows = {row['symbol']: row for row in targets.to_dicts()}
    all_symbols = set(rows) | set(pos_map)

    plans: list[RebalanceOrderPlan] = []
    for symbol in sorted(all_symbols):
        price = float(latest_prices.get(symbol, 0.0) or 0.0)
        if price <= 0:
            continue

        current_qty = float(pos_map.get(symbol).qty if symbol in pos_map else 0.0)
        target_weight = float(rows.get(symbol, {}).get('target_weight', 0.0) or 0.0)
        if long_only:
            target_weight = max(target_weight, 0.0)
        current_weight = (current_qty * price / equity) if equity > 0 else 0.0
        weight_diff = target_weight - current_weight
        if abs(weight_diff) < weight_tolerance:
            continue
        target_qty = equity * target_weight / price
        delta_qty = target_qty - current_qty
        trade_value = abs(delta_qty) * price

        if trade_value < min_trade_value:
            continue
        if abs(delta_qty) < 1e-8:
            continue

        plans.append(
            RebalanceOrderPlan(
                symbol=symbol,
                side='BUY' if delta_qty > 0 else 'SELL',
                qty=abs(delta_qty),
                target_weight=target_weight,
                current_qty=current_qty,
                target_qty=target_qty,
                latest_price=price,
                execution_reference_price=price,
                estimated_trade_value=trade_value,
            )
        )
    return plans
