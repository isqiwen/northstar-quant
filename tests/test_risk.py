from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

from northstar_quant.data.research import Market
from northstar_quant.risk import Outcome, PortfolioState, RiskPolicy, Side, evaluate_risk
from northstar_quant.strategy import StrategyIntent

AT = datetime(2026, 1, 5, 1, tzinfo=UTC)
MARKET = Market(UUID(int=200), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60)
POLICY = RiskPolicy(
    10, Decimal("1000000"), Decimal("0.5"), Decimal("0.1"), Decimal("0.1"), Decimal(2), 1
)


def intent(target: str = "1") -> StrategyIntent:
    return StrategyIntent(
        UUID(int=1),
        MARKET.contract_id,
        AT,
        AT + timedelta(minutes=5),
        Decimal("0.1"),
        Decimal(target),
    )


def test_sizing_reserves_fees_and_slippage_before_margin_is_authorized() -> None:
    # One lot has 100 margin but fees+slippage leave only 98 equity at the mark.
    state = PortfolioState(AT, Decimal(110), 0, Decimal(100))
    policy = replace(POLICY, max_margin_fraction=Decimal(1), max_lots=1)
    decision = evaluate_risk(intent(), state, policy, MARKET)
    assert decision.outcome is Outcome.REJECT
    assert decision.quantity_lots == 0

    state = replace(state, equity=Decimal(112))
    for target in ("1", "-1"):
        decision = evaluate_risk(intent(target), state, policy, MARKET)
        assert decision.outcome is Outcome.ALLOW
        assert decision.quantity_lots == 1
        assert decision.minimum_fill_price is not None and decision.maximum_fill_price is not None
        direction = 1 if decision.side is Side.BUY else -1
        with localcontext() as context:
            context.prec = 96
            for fill in (decision.minimum_fill_price, decision.maximum_fill_price):
                mark = fill - direction * MARKET.price_tick
                equity = state.equity - policy.fee_per_lot - MARKET.multiplier * MARKET.price_tick
                margin = mark * MARKET.multiplier * policy.initial_margin_fraction
                assert equity > 0 and margin <= equity * policy.max_margin_fraction


def test_reversal_reduces_to_flat_and_identity_or_stale_account_never_authorizes() -> None:
    state = PortfolioState(AT, Decimal(100000), 5, Decimal(100))
    decision = evaluate_risk(intent("-0.8"), state, POLICY, MARKET)
    assert (
        decision.outcome,
        decision.approved_position_lots,
        decision.side,
        decision.quantity_lots,
    ) == (Outcome.REDUCE, 0, Side.SELL, 5)
    unknown = evaluate_risk(replace(intent(), contract_id=UUID(int=999)), state, POLICY, MARKET)
    assert unknown.outcome is Outcome.UNKNOWN and unknown.quantity_lots == 0
    stale = evaluate_risk(
        intent(), replace(state, observed_at=AT - timedelta(seconds=1)), POLICY, MARKET
    )
    assert stale.outcome is Outcome.UNKNOWN and stale.quantity_lots == 0


def test_public_tick_bound_stays_inside_financial_limit_after_precision_reduction() -> None:
    market = replace(MARKET, price_tick=Decimal("0.000000000000000003"), multiplier=Decimal("0.03"))
    state = PortfolioState(
        AT, Decimal("9999999999999999999999999999999999"), 0, Decimal("200000000000000000001")
    )
    policy = replace(
        POLICY,
        max_lots=1,
        max_gross_notional=Decimal("10000000000000000000.00000000000001"),
        max_margin_fraction=Decimal(1),
        initial_margin_fraction=Decimal("0.03"),
        max_adverse_price_move_fraction=Decimal("0.9"),
        fee_per_lot=Decimal(0),
        slippage_ticks=0,
    )
    decision = evaluate_risk(intent(), state, policy, market)
    maximum = decision.maximum_fill_price
    assert maximum == Decimal("333333333333333333333.3333333333336")
    numerator, denominator = maximum.as_integer_ratio()
    tick_numerator, tick_denominator = market.price_tick.as_integer_ratio()
    assert (numerator * tick_denominator) % (denominator * tick_numerator) == 0
    with localcontext() as context:
        context.prec = 96
        assert maximum * market.multiplier <= policy.max_gross_notional
