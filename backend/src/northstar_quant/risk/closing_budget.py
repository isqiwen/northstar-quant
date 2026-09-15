"""Bound a one-lot reduction without spending anticipated margin release."""

from decimal import ROUND_CEILING, Decimal, localcontext

from northstar_quant.accounting.terms import ChargeRate
from northstar_quant.execution.orders import OrderBudget, Side


def closing_budget(
    *,
    side: Side,
    limit_price: Decimal,
    last_price: Decimal,
    price_tick: Decimal,
    multiplier: Decimal,
    lower_limit: Decimal,
    upper_limit: Decimal,
    fee: ChargeRate,
    max_adverse_fraction: Decimal,
) -> OrderBudget:
    with localcontext() as context:
        context.prec = 192
        values = (limit_price, last_price, price_tick, multiplier, lower_limit, upper_limit)
        if any(not value.is_finite() or value <= 0 for value in values):
            raise ValueError("closing requires positive exact market values")
        if (
            not isinstance(side, Side)
            or not max_adverse_fraction.is_finite()
            or not Decimal(0) <= max_adverse_fraction <= Decimal(1)
            or not lower_limit <= limit_price <= upper_limit
            or not lower_limit <= last_price <= upper_limit
            or any(
                value % price_tick for value in (limit_price, last_price, lower_limit, upper_limit)
            )
        ):
            raise ValueError("closing price is outside the verified market bounds")
        adverse = limit_price - last_price if side is Side.BUY else last_price - limit_price
        if adverse > last_price * max_adverse_fraction:
            raise ValueError("closing exceeds the adverse-price limit")
        # A sell limit can fill higher. The upper daily bound covers the fee;
        # the limit bounds a buy. This is a reservation, not a charged fee.
        maximum = limit_price if side is Side.BUY else upper_limit
        amount = fee.amount(maximum * multiplier, 1).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        return OrderBudget(amount, Decimal(0), Decimal(0), Decimal(0))
