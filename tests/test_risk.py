from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from northstar_quant.data.research import Market
from northstar_quant.risk import (
    OpeningAccount,
    OpeningCandidate,
    OpeningLimits,
    OpeningTerms,
    Outcome,
    PortfolioState,
    RiskPolicy,
    Side,
    evaluate_opening_budget,
    evaluate_risk,
)
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


def opening_inputs() -> tuple[OpeningAccount, OpeningTerms, OpeningLimits, OpeningCandidate]:
    return (
        OpeningAccount(Decimal(10000), Decimal(10000), Decimal(0)),
        OpeningTerms(
            price_tick=Decimal(1),
            multiplier=Decimal(10),
            long_margin_by_money=Decimal("0.1"),
            long_margin_by_volume=Decimal(2),
            short_margin_by_money=Decimal("0.2"),
            short_margin_by_volume=Decimal(3),
            open_fee_by_money=Decimal("0.0001"),
            open_fee_by_volume=Decimal("0.5"),
            lower_limit=Decimal(90),
            upper_limit=Decimal(120),
            last_price=Decimal(100),
            pre_settlement_price=Decimal(100),
            min_limit_lots=1,
            max_limit_lots=10,
        ),
        OpeningLimits(10, Decimal(2000), Decimal("0.5"), Decimal("0.1")),
        OpeningCandidate(Side.BUY, Decimal(100)),
    )


def test_opening_budget_uses_actual_available_and_sell_daily_upper_bound() -> None:
    account, terms, limits, candidate = opening_inputs()
    account = replace(account, available=Decimal(200))
    buy = evaluate_opening_budget(account, terms, limits, candidate)
    sell = evaluate_opening_budget(account, terms, limits, replace(candidate, side=Side.SELL))
    assert buy["outcome"] == "WITHIN_BUDGET" and buy["reasons"] == []
    assert buy["requested_price"] == buy["reservation_price"] == "100"
    assert buy["margin_reference_price"] == sell["margin_reference_price"] == "120"
    assert buy["margin_budget"] == "122" and buy["fee_budget"] == "0.6"
    assert buy["capital_budget"] == "122.6" and buy["available_after_budget"] == "77.4"
    assert sell["requested_price"] == "100" and sell["reservation_price"] == "120"
    assert sell["notional"] == "1200" and sell["margin_budget"] == "243"
    assert sell["fee_budget"] == "0.62" and sell["capital_budget"] == "243.62"
    assert sell["outcome"] == "REJECT" and sell["reasons"] == ["INSUFFICIENT_AVAILABLE"]
    gross = evaluate_opening_budget(
        replace(account, available=Decimal(10000)),
        terms,
        replace(limits, max_gross_notional=Decimal(1199)),
        replace(candidate, side=Side.SELL),
    )
    assert gross["reasons"] == ["GROSS_NOTIONAL_LIMIT_EXCEEDED"]
    higher_settlement = evaluate_opening_budget(
        account, replace(terms, pre_settlement_price=Decimal(150)), limits, candidate
    )
    assert higher_settlement["margin_reference_price"] == "150"
    assert higher_settlement["margin_budget"] == "152"
    assert higher_settlement["notional"] == "1000" and higher_settlement["fee_budget"] == "0.6"
    for result in (buy, sell, gross):
        assert result["execution"] == {"order_sending": False, "cancel_sending": False}
        assert result["scope"] == "OPENING_BUDGET_PRECHECK" and result["quantity_lots"] == 1


def test_opening_budget_rounds_costs_up_and_accounts_for_existing_margin_after_fees() -> None:
    _, terms, limits, candidate = opening_inputs()
    terms = replace(
        terms,
        long_margin_by_money=Decimal("0.01"),
        long_margin_by_volume=Decimal("0.005"),
        open_fee_by_money=Decimal("0.000001"),
        open_fee_by_volume=Decimal("0.005"),
    )
    account = OpeningAccount(Decimal("24.63"), Decimal("12.02"), Decimal("0.30"))
    with localcontext() as context:
        context.prec = 6
        result = evaluate_opening_budget(account, terms, limits, candidate)
        less_available = evaluate_opening_budget(
            replace(account, available=Decimal("12.019999999999999999")), terms, limits, candidate
        )
        less_equity = evaluate_opening_budget(
            replace(account, equity=Decimal("24.629999999999999999")), terms, limits, candidate
        )
    assert result["outcome"] == "WITHIN_BUDGET"
    assert result["margin_budget"] == "12.01" and result["fee_budget"] == "0.01"
    assert result["capital_budget"] == "12.02" and result["total_margin_budget"] == "12.31"
    assert result["available_after_budget"] == "0"  # Existing margin is not deducted twice.
    assert less_available["reasons"] == ["INSUFFICIENT_AVAILABLE"]
    assert less_equity["reasons"] == ["MARGIN_FRACTION_EXCEEDED"]
    insufficient_equity = evaluate_opening_budget(
        replace(account, equity=Decimal(10)), terms, limits, candidate
    )
    assert "INSUFFICIENT_EQUITY" in insufficient_equity["reasons"]


def test_opening_budget_rejects_order_size_price_tick_limits_and_adverse_prices() -> None:
    account, terms, limits, candidate = opening_inputs()
    cases = (
        (terms, limits, replace(candidate, lots=2), "ONLY_ONE_OPENING_LOT_SUPPORTED"),
        (replace(terms, min_limit_lots=2), limits, candidate, "INSTRUMENT_LOT_LIMIT"),
        (terms, replace(limits, max_lots=1), replace(candidate, lots=2), "MAX_LOTS_EXCEEDED"),
        (terms, limits, replace(candidate, limit_price=Decimal("100.5")), "PRICE_OFF_TICK"),
        (terms, limits, replace(candidate, limit_price=Decimal(121)), "PRICE_OUTSIDE_DAILY_LIMITS"),
        (
            terms,
            limits,
            replace(candidate, limit_price=Decimal(111)),
            "ADVERSE_PRICE_LIMIT_EXCEEDED",
        ),
        (
            terms,
            replace(limits, max_adverse_price_move_fraction=Decimal("0.05")),
            OpeningCandidate(Side.SELL, Decimal(94)),
            "ADVERSE_PRICE_LIMIT_EXCEEDED",
        ),
    )
    for current_terms, current_limits, current_candidate, reason in cases:
        result = evaluate_opening_budget(account, current_terms, current_limits, current_candidate)
        assert result["outcome"] == "REJECT" and reason in result["reasons"]
    for side, price in ((Side.BUY, "110"), (Side.SELL, "90")):
        result = evaluate_opening_budget(
            account, terms, limits, OpeningCandidate(side, Decimal(price))
        )
        assert result["outcome"] == "WITHIN_BUDGET"  # Exact adverse-price boundary is allowed.


@pytest.mark.parametrize(
    "value", [None, Decimal("NaN"), Decimal("Infinity"), Decimal("1e-19"), Decimal("1e34")]
)
def test_opening_budget_never_replaces_unknown_or_unbounded_costs_with_zero(value: Decimal) -> None:
    _, terms, _, _ = opening_inputs()
    with pytest.raises(ValueError):
        replace(terms, open_fee_by_money=value)
