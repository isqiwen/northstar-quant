"""Generate simulated fills; account facts are applied by Accounting."""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from northstar_quant.accounting.fifo import FillFact
from northstar_quant.execution.orders import PendingOrder, Side
from northstar_quant.market_data import Market, MarketBar


def simulate_fill(
    order: PendingOrder,
    bar: MarketBar,
    market: Market,
    *,
    fee_per_lot: Decimal,
    slippage_ticks: int,
) -> FillFact | None:
    """Use a later completed/available bar and adverse slippage, filling all lots.

    This is the current explicit simulation model, not an external execution
    report. It does not change cash, positions or persistent order state.
    """

    if (
        not fee_per_lot.is_finite()
        or fee_per_lot < 0
        or type(slippage_ticks) is not int
        or slippage_ticks < 0
    ):
        raise ValueError("simulation costs require nonnegative fee and integer ticks")
    if (
        bar.observation_id == order.observation_id
        or bar.completed_at <= order.submitted_at
        or not order.submitted_at < bar.available_at < order.expires_at
    ):
        return None
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        direction = 1 if order.side is Side.BUY else -1
        price = bar.close + direction * slippage_ticks * market.price_tick
        if price <= 0 or not order.minimum_fill_price <= price <= order.maximum_fill_price:
            return None
        identity = hashlib.sha256(
            f"simulation:{order.order_id}:{bar.observation_id}".encode()
        ).hexdigest()
        return FillFact(
            identity,
            order.order_id,
            market.contract_id,
            bar.observation_id,
            bar.available_at,
            bar.trading_day,
            order.side,
            order.offset,
            order.quantity_lots,
            price,
            order.quantity_lots * fee_per_lot,
        )
