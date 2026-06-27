"""分钟级再平衡计划器。"""

from __future__ import annotations

import math

import polars as pl

from northstar_quant.config.settings import get_settings
from northstar_quant.execution.models import PositionSnapshot, RebalanceOrderPlan


def build_intraday_rebalance_plan(
    targets: pl.DataFrame,
    positions: list[PositionSnapshot],
    latest_prices: dict[str, float],
    equity: float | None = None,
) -> list[RebalanceOrderPlan]:
    """根据分钟级目标仓位与真实持仓生成日内执行计划。"""

    settings = get_settings()
    equity = float(equity or settings.default_cash)
    pos_map = {p.symbol: p for p in positions}
    rows = {row["symbol"]: row for row in targets.to_dicts()}
    all_symbols = set(rows) | set(pos_map)

    plans: list[RebalanceOrderPlan] = []
    for symbol in sorted(all_symbols):
        price = float(latest_prices.get(symbol, 0.0) or 0.0)
        if price <= 0:
            continue

        current_qty = float(pos_map.get(symbol).qty if symbol in pos_map else 0.0)
        target_weight = float(rows.get(symbol, {}).get("target_weight", 0.0) or 0.0)
        target_qty = math.floor(equity * target_weight / price)
        delta_qty = target_qty - current_qty
        rounded_delta_qty = int(round(delta_qty))
        trade_value = abs(rounded_delta_qty) * price

        if trade_value < settings.rebalance_min_trade_value:
            continue
        if rounded_delta_qty == 0:
            continue

        plans.append(
            RebalanceOrderPlan(
                symbol=symbol,
                side="BUY" if rounded_delta_qty > 0 else "SELL",
                qty=float(abs(rounded_delta_qty)),
                target_weight=target_weight,
                current_qty=current_qty,
                target_qty=float(target_qty),
                latest_price=price,
                execution_reference_price=price,
                estimated_trade_value=trade_value,
                strategy_id="intraday_portfolio",
                reason="intraday_rebalance",
            )
        )
    return plans
