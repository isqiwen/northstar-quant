"""Generate simulated fills; account facts are applied by Accounting."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext

from northstar_quant.accounting.fifo import FillFact
from northstar_quant.execution.orders import PendingOrder, Side
from northstar_quant.market_data import Market, MarketBar


@dataclass(frozen=True, slots=True)
class FillAttempt:
    fill: FillFact | None
    reason: str


def simulate_fill(
    order: PendingOrder,
    bar: MarketBar,
    market: Market,
    *,
    fee_per_lot: Decimal,
    slippage_ticks: int,
    max_volume_participation: Decimal,
) -> FillAttempt:
    """Use only a complete post-order bar's volume, bounded by fixed participation.

    One order is evaluated per bar by the current research loop. This model
    cannot establish intrabar queue priority or actual exchange liquidity.
    """

    bar.validate(interval_seconds=market.interval_seconds, price_tick=market.price_tick)
    if (
        not isinstance(max_volume_participation, Decimal)
        or not max_volume_participation.is_finite()
        or not Decimal(0) < max_volume_participation <= Decimal(1)
        or not fee_per_lot.is_finite()
        or fee_per_lot < 0
        or type(slippage_ticks) is not int
        or slippage_ticks < 0
    ):
        raise ValueError("simulation costs require nonnegative fee and integer ticks")
    if bar.available_at >= order.expires_at:
        return FillAttempt(None, "EXPIRED")
    if order.remaining_lots == 0:
        return FillAttempt(None, "ALREADY_FILLED")
    if (
        bar.event_time < order.submitted_at
        or bar.observation_id == order.observation_id
        or bar.completed_at <= order.submitted_at
        or not order.submitted_at < bar.available_at < order.expires_at
    ):
        return FillAttempt(None, "NO_POST_ORDER_VOLUME")
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        quantity = min(
            order.remaining_lots,
            int((bar.volume * max_volume_participation).to_integral_value(rounding=ROUND_FLOOR)),
        )
        if quantity == 0:
            return FillAttempt(None, "NO_EXECUTABLE_VOLUME")
        direction = 1 if order.side is Side.BUY else -1
        price = bar.close + direction * slippage_ticks * market.price_tick
        if price <= 0 or not order.minimum_fill_price <= price <= order.maximum_fill_price:
            return FillAttempt(None, "PRICE_OUTSIDE_AUTHORIZATION")
        identity = hashlib.sha256(
            f"simulation:{order.order_id}:{bar.observation_id}".encode()
        ).hexdigest()
        fill = FillFact(
            identity,
            order.order_id,
            market.contract_id,
            bar.observation_id,
            bar.available_at,
            bar.trading_day,
            order.side,
            order.offset,
            quantity,
            price,
            quantity * fee_per_lot,
        )

        return FillAttempt(
            fill, "FILLED" if quantity == order.remaining_lots else "PARTIALLY_FILLED"
        )
