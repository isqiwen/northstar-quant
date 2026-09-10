"""Conservative risk envelopes from the same charges used for simulated facts."""

from dataclasses import replace
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, localcontext

from northstar_quant.accounting.terms import FuturesTerms
from northstar_quant.execution.orders import Offset, Side
from northstar_quant.market_data import Market

from .sizing import RiskPolicy


def order_budget(
    policy: RiskPolicy,
    market: Market,
    *,
    side: Side,
    offset: Offset,
    maximum_fill_price: Decimal,
    terms: FuturesTerms | None,
) -> tuple[Decimal, Decimal]:
    """Bound per-lot fee and new margin before the owner fixes an order.

    Upward one-lot rounding covers separately charged partial fills. Close
    orders reserve their inventory and fee, without spending anticipated margin
    release. These are budgets, never posted fees or broker frozen-fund facts.
    """
    with localcontext() as context:
        context.prec = 192
        context.rounding = ROUND_HALF_EVEN
        if not maximum_fill_price.is_finite() or maximum_fill_price <= 0:
            raise ValueError("order budget requires a bounded positive authorized price")
        mark_bound = maximum_fill_price + policy.slippage_ticks * market.price_tick
        if terms is None:
            return policy.fee_per_lot, (
                mark_bound * market.multiplier * policy.initial_margin_fraction
                if offset is Offset.OPEN
                else Decimal(0)
            )
        if terms.contract_id != market.contract_id:
            raise ValueError("order budget terms belong to another contract")
        rate = {
            Offset.OPEN: terms.open_fee,
            Offset.CLOSE_TODAY: terms.close_today_fee,
            Offset.CLOSE_YESTERDAY: terms.close_yesterday_fee,
        }[offset]
        fee = (
            rate.amount(maximum_fill_price * market.multiplier, 1) / terms.money_quantum
        ).to_integral_value(rounding=ROUND_CEILING) * terms.money_quantum
        margin_rate = terms.long_margin if side is Side.BUY else terms.short_margin
        margin = (
            margin_rate.amount(mark_bound * market.multiplier, 1) / terms.money_quantum
        ).to_integral_value(rounding=ROUND_CEILING) * terms.money_quantum
        return fee, margin if offset is Offset.OPEN else Decimal(0)


def policy_for_terms(policy: RiskPolicy, terms: FuturesTerms, market: Market) -> RiskPolicy:
    """Bound every fee/side over the fixed daily price interval, including rounding.

    A one-lot upward fee bound also covers individually rounded partial fills.
    The margin fraction includes volume charges and one rounding quantum at the
    lowest admissible price. It is a budget envelope, never a posted account fee.
    """
    if terms.contract_id != market.contract_id:
        raise ValueError("risk terms belong to a different contract")
    with localcontext() as context:
        context.prec = 192
        context.rounding = ROUND_HALF_EVEN
        lower_notional = terms.lower_limit * market.multiplier
        upper_notional = terms.upper_limit * market.multiplier
        fee = max(
            (rate.amount(upper_notional, 1) / terms.money_quantum).to_integral_value(
                rounding=ROUND_CEILING
            )
            * terms.money_quantum
            for rate in (terms.open_fee, terms.close_today_fee, terms.close_yesterday_fee)
        )
        margin = max(
            rate.by_money + (rate.by_volume + terms.money_quantum) / lower_notional
            for rate in (terms.long_margin, terms.short_margin)
        )
        return replace(policy, fee_per_lot=fee, initial_margin_fraction=margin)
