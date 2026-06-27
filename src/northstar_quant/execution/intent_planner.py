"""Translate execution intents into executable order plans."""

from __future__ import annotations

import polars as pl

from northstar_quant.common.enums import OrderSemantic
from northstar_quant.config.settings import get_settings
from northstar_quant.execution.intent_semantics import resolve_execution_intent_qty
from northstar_quant.execution.models import PositionSnapshot, RebalanceOrderPlan


def build_execution_intent_plan(
    intents: pl.DataFrame,
    positions: list[PositionSnapshot],
    latest_prices: dict[str, float],
    equity: float | None = None,
) -> list[RebalanceOrderPlan]:
    """Build direct order plans from execution intents."""

    settings = get_settings()
    equity = float(equity or settings.default_cash)
    position_map = {item.symbol: float(item.qty) for item in positions}

    plans: list[RebalanceOrderPlan] = []
    for row in intents.to_dicts():
        symbol = str(row["symbol"])
        price = float(latest_prices.get(symbol, 0.0) or 0.0)
        if price <= 0:
            continue

        size_fraction = float(row.get("size_fraction", 0.0) or 0.0)
        if size_fraction <= 0:
            continue

        side = str(row["side"]).upper()
        order_semantic = str(row.get("order_semantic") or OrderSemantic.ENTRY.value).lower()
        current_qty = float(position_map.get(symbol, 0.0))
        qty = resolve_execution_intent_qty(
            side=side,
            order_semantic=order_semantic,
            size_fraction=size_fraction,
            price=price,
            equity=equity,
            current_qty=current_qty,
        )
        if qty <= 0:
            continue

        trade_value = qty * price
        if trade_value < settings.rebalance_min_trade_value:
            continue

        plans.append(
            RebalanceOrderPlan(
                symbol=symbol,
                side=side,
                qty=float(qty),
                target_weight=None,
                current_qty=current_qty,
                target_qty=None,
                latest_price=price,
                execution_reference_price=price,
                estimated_trade_value=float(trade_value),
                strategy_id=str(row.get("strategy_id") or "execution_intent"),
                order_semantic=order_semantic,
                reason=str(row.get("reason") or order_semantic),
                order_type=str(row.get("order_type") or "MKT").upper(),
                limit_price=(
                    float(row["limit_price"])
                    if row.get("limit_price") is not None
                    else None
                ),
            )
        )
    return plans
