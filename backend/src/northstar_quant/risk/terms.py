"""Conservative risk envelopes from the same charges used for simulated facts."""

from dataclasses import replace
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, localcontext

from northstar_quant.accounting.terms import FuturesTerms
from northstar_quant.market_data import Market

from .sizing import RiskPolicy


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
