from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from northstar_quant.accounting.fifo import Account, FillFact
from northstar_quant.accounting.positions import Position
from northstar_quant.execution.orders import Offset, PendingOrder, Side, order_slice
from northstar_quant.market_data import Market, MarketBar
from northstar_quant.simulation import simulate_fill


def test_fill_enforces_actual_slipped_price_and_fifo_cost_conservation() -> None:
    at = datetime(2026, 1, 5, 1, tzinfo=UTC)
    market = Market(
        UUID(int=1), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60
    )
    account = Account(Decimal(1000), market)
    order = PendingOrder(
        "open",
        UUID(int=10),
        at,
        at + timedelta(minutes=10),
        Side.BUY,
        Offset.OPEN,
        2,
        Decimal(100),
        Decimal(102),
        fee_budget_per_lot=Decimal(2),
        margin_budget_per_lot=Decimal(102),
    )
    bar = MarketBar(
        UUID(int=11),
        at,
        at + timedelta(minutes=1),
        at + timedelta(minutes=1, seconds=1),
        date(2026, 1, 5),
        Decimal(102),
        Decimal(100),
    )
    assert (
        simulate_fill(
            order,
            bar,
            market,
            fee_per_lot=Decimal(2),
            slippage_ticks=1,
            max_volume_participation=Decimal("0.1"),
        ).fill
        is None
    )
    fact = simulate_fill(
        order,
        bar,
        market,
        fee_per_lot=Decimal(2),
        slippage_ticks=0,
        max_volume_participation=Decimal("0.1"),
    ).fill
    assert fact is not None and fact.price == Decimal(102)
    assert account.position_lots == 0
    fill = account.apply(fact)
    assert account.apply(fact) == fill
    closing = PendingOrder(
        "close",
        bar.observation_id,
        bar.available_at,
        at + timedelta(minutes=10),
        Side.SELL,
        Offset.CLOSE_TODAY,
        2,
        Decimal(100),
        Decimal(110),
        fee_budget_per_lot=Decimal(2),
        margin_budget_per_lot=Decimal(0),
    )
    later = MarketBar(
        UUID(int=12),
        at + timedelta(minutes=2),
        at + timedelta(minutes=3),
        at + timedelta(minutes=3, seconds=1),
        date(2026, 1, 5),
        Decimal(105),
        Decimal(100),
    )
    closing_fact = simulate_fill(
        closing,
        later,
        market,
        fee_per_lot=Decimal(2),
        slippage_ticks=1,
        max_volume_participation=Decimal("0.1"),
    ).fill
    assert closing_fact is not None
    account.apply(closing_fact)
    assert account.realized_pnl == Decimal(40)
    assert account.total_fees == Decimal(8)
    assert account.cash == account.equity(later.close) == Decimal(1032)
    assert account.position_lots == 0


def test_account_applies_individual_facts_not_whole_orders_or_bar_guesses() -> None:
    at = datetime(2026, 1, 5, 1, tzinfo=UTC)
    market = Market(
        UUID(int=1), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60
    )
    account = Account(Decimal(1000), market)
    first = FillFact(
        "fill-1",
        "open",
        market.contract_id,
        None,
        at,
        date(2026, 1, 5),
        Side.BUY,
        Offset.OPEN,
        2,
        Decimal(100),
        Decimal(2),
    )
    second = replace(first, fill_id="fill-2", quantity_lots=1, price=Decimal(110), fee=Decimal(1))
    first_applied = account.apply(first)
    account.apply(second)
    partial_close = replace(
        first,
        fill_id="fill-3",
        order_id="close",
        side=Side.SELL,
        offset=Offset.CLOSE_TODAY,
        quantity_lots=1,
        price=Decimal(120),
        fee=Decimal(1),
    )
    assert account.apply(partial_close).realized_pnl == Decimal(200)
    reversal = replace(
        partial_close, fill_id="fill-4", quantity_lots=2, price=Decimal(90), fee=Decimal(2)
    )
    assert account.apply(reversal).realized_pnl == Decimal(-300)
    account.apply(
        replace(reversal, fill_id="fill-5", offset=Offset.OPEN, quantity_lots=1, fee=Decimal(1))
    )
    assert account.position_lots == -1
    assert account.cash == Decimal(893)
    assert account.total_fees == Decimal(7)
    assert account.equity(Decimal(85)) == Decimal(943)
    state = account.checkpoint()
    assert account.apply(FillFact.from_dict(first_applied.to_dict())) == first_applied
    assert account.checkpoint() == state
    with pytest.raises(ValueError, match="reused"):
        account.apply(replace(first, price=Decimal(101)))
    with pytest.raises(ValueError, match="contract"):
        account.apply(replace(first, fill_id="other", contract_id=UUID(int=2)))
    assert account.checkpoint() == state


def test_gross_opens_explicit_closes_and_broker_projection_share_quantities() -> None:
    from northstar_quant.accounting.positions import PositionChange, project_intraday_positions

    at = datetime(2026, 1, 5, 1, tzinfo=UTC)
    market = Market(
        UUID(int=1), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60
    )
    account = Account(Decimal(1000), market)
    first = FillFact(
        "long",
        "open",
        market.contract_id,
        None,
        at,
        at.date(),
        Side.BUY,
        Offset.OPEN,
        2,
        Decimal(100),
        Decimal(2),
    )
    short = replace(
        first, fill_id="short", side=Side.SELL, quantity_lots=1, price=Decimal(110), fee=Decimal(1)
    )
    facts = [first, short]
    for fact in facts:
        account.apply(fact)
    assert account.position.long_today == 2 and account.position.short_today == 1
    assert account.realized_pnl == 0
    assert account.cash == Decimal(997)
    assert account.equity(Decimal(105)) == Decimal(1147)
    before = account.checkpoint()
    for invalid in (
        replace(short, fill_id="overclose", offset=Offset.CLOSE_TODAY, quantity_lots=3),
        replace(short, fill_id="missing-yesterday", offset=Offset.CLOSE_YESTERDAY),
        replace(short, fill_id="next-day", trading_day=at.date() + timedelta(days=1)),
    ):
        with pytest.raises(ValueError):
            account.apply(invalid)
        assert account.checkpoint() == before
    close = replace(
        short,
        fill_id="close-long",
        offset=Offset.CLOSE_TODAY,
        price=Decimal(120),
        filled_at=at + timedelta(seconds=1),
    )
    applied = account.apply(close)
    facts.append(close)
    assert applied.realized_pnl == Decimal(200)
    assert applied.gross_position.long_today == applied.gross_position.short_today == 1
    assert account.position_lots == 0  # Flat net exposure still has two gross holdings.
    assert account.equity(Decimal(105)) == Decimal(1296)
    from northstar_quant.accounting.portfolio import value_account

    valuation = value_account(account, Decimal(105), at=close.filled_at)
    assert valuation.long_lots == valuation.short_lots == 1
    assert valuation.net_exposure == 0 and valuation.gross_exposure == Decimal(2100)
    assert valuation.trade_realized_pnl == Decimal(200) and valuation.settlement_pnl == 0
    assert valuation.equity == Decimal(1296)
    assert valuation.margin_used is None and "available" not in valuation.to_dict()
    with pytest.raises(ValueError, match="precede"):
        value_account(account, Decimal(105), at=at)
    changes = tuple(
        PositionChange(
            f.contract_id, f.trading_day, f.side.value, f.offset.value, f.quantity_lots, f.filled_at
        )
        for f in facts
    )
    assert (
        project_intraday_positions(at.date(), changes)[market.contract_id]
        == account.position.to_dict()
    )
    rebuilt = Account(Decimal(1000), market)
    for fact in facts:
        rebuilt.apply(FillFact.from_dict(fact.to_dict()))
    assert rebuilt.checkpoint() == account.checkpoint()
    before = account.checkpoint()
    with pytest.raises(RuntimeError, match="rollback"):
        with account.transaction():
            account.apply(replace(close, fill_id="rollback-close"))
            raise RuntimeError("rollback")
    assert account.checkpoint() == before
    assert order_slice(Position(long_today=1), Side.SELL, 1) == (Offset.CLOSE_TODAY, 1)
    with pytest.raises(ValueError, match="cross through zero"):
        order_slice(Position(long_today=1), Side.SELL, 2)


def test_broker_timestamp_group_preserves_unknown_fill_order_and_position_age() -> None:
    from northstar_quant.accounting.positions import (
        Position,
        PositionChange,
        project_intraday_positions,
    )

    at = datetime(2026, 1, 5, 1, tzinfo=UTC)
    opening = PositionChange(UUID(int=1), at.date(), "BUY", "OPEN", 2, at)
    closing = replace(opening, direction="SELL", offset="CLOSE_TODAY", quantity_lots=1)
    assert project_intraday_positions(at.date(), (closing, opening))[UUID(int=1)]["long_today"] == 1
    with pytest.raises(ValueError, match="exceed"):
        project_intraday_positions(
            at.date(), (closing, replace(opening, filled_at=at + timedelta(seconds=1)))
        )
    established = Position(long_today=2, long_yesterday=3)
    result = established.apply((replace(closing, offset="CLOSE_YESTERDAY"),))
    assert result.long_today == 2 and result.long_yesterday == 2


def test_participation_uses_only_post_order_volume_and_explains_each_rejection() -> None:
    at = datetime(2026, 1, 5, 1, tzinfo=UTC)
    market = Market(
        UUID(int=1), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60
    )
    order = PendingOrder(
        "partial",
        UUID(int=10),
        at,
        at + timedelta(minutes=10),
        Side.BUY,
        Offset.OPEN,
        5,
        Decimal(90),
        Decimal(110),
        fee_budget_per_lot=Decimal(2),
        margin_budget_per_lot=Decimal(110),
    )
    bar = MarketBar(
        UUID(int=11),
        at,
        at + timedelta(minutes=1),
        at + timedelta(minutes=1),
        at.date(),
        Decimal(100),
        Decimal(19),
    )

    def attempt(request=order, observation=bar):
        return simulate_fill(
            request,
            observation,
            market,
            fee_per_lot=Decimal(2),
            slippage_ticks=0,
            max_volume_participation=Decimal("0.1"),
        )

    result = attempt()
    assert result.reason == "PARTIALLY_FILLED"
    assert result.fill.quantity_lots == 1
    assert result.fill.fee == Decimal(2)
    remaining = replace(order, filled_lots=1)
    second = attempt(remaining, replace(bar, observation_id=UUID(int=12), volume=Decimal(100)))
    assert second.fill.quantity_lots == 4
    assert second.reason == "FILLED"
    assert result.fill.fill_id != second.fill.fill_id
    assert attempt(observation=replace(bar, volume=Decimal(0))).reason == "NO_EXECUTABLE_VOLUME"
    assert (
        attempt(request=replace(order, submitted_at=at + timedelta(seconds=1))).reason
        == "NO_POST_ORDER_VOLUME"
    )
    assert (
        attempt(observation=replace(bar, close=Decimal(111))).reason
        == "PRICE_OUTSIDE_AUTHORIZATION"
    )
    assert attempt(request=replace(order, expires_at=bar.available_at)).reason == "EXPIRED"
    assert attempt(request=replace(order, filled_lots=5)).reason == "ALREADY_FILLED"


@pytest.mark.parametrize("side", list(Side))
@pytest.mark.parametrize("offset", [Offset.OPEN, Offset.CLOSE_TODAY])
def test_limit_queue_keeps_order_and_reservation_without_inventing_a_fill(side, offset):
    from northstar_quant.execution.orders import reservation
    from tests.accounting.test_terms import terms

    fixed = terms()
    at = fixed.effective_from + timedelta(hours=1)
    market = Market(
        fixed.contract_id, "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60
    )
    order = PendingOrder(
        "limit-queue",
        UUID(int=10),
        at,
        at + timedelta(minutes=10),
        side,
        offset,
        5,
        fixed.lower_limit,
        fixed.upper_limit,
        fee_budget_per_lot=Decimal(4),
        margin_budget_per_lot=Decimal(300) if offset is Offset.OPEN else Decimal(0),
    )
    price = fixed.upper_limit if side is Side.BUY else fixed.lower_limit
    bar = MarketBar(
        UUID(int=11),
        at,
        at + timedelta(minutes=1),
        at + timedelta(minutes=1),
        at.date(),
        price,
        Decimal(1000),
    )

    def attempt(request, observation):
        return simulate_fill(
            request,
            observation,
            market,
            fee_per_lot=Decimal(2),
            slippage_ticks=0,
            max_volume_participation=Decimal("0.1"),
            terms=fixed,
        )

    before = reservation(order)
    blocked = attempt(order, bar)
    assert blocked.fill is None and blocked.reason == "LIMIT_QUEUE_UNOBSERVED"
    assert reservation(order) == before
    # Trading away from the adverse queue can fill only available participation.
    later = replace(
        bar,
        observation_id=UUID(int=12),
        event_time=bar.completed_at,
        completed_at=bar.completed_at + timedelta(minutes=1),
        available_at=bar.available_at + timedelta(minutes=1),
        close=price - 1 if side is Side.BUY else price + 1,
        volume=Decimal(10),
    )
    filled = attempt(order, later)
    assert filled.fill is not None and filled.fill.quantity_lots == 1
    remaining = order.record_fill(1, at=later.available_at, reason=filled.reason).order
    assert Decimal(reservation(remaining)["reserved_fee"]) == Decimal(
        before["reserved_fee"]
    ) * Decimal("0.8")
    # A daily limit is directional; selling into the upper limit is not buying
    # into its unobserved queue (and vice versa at the lower limit).
    opposite = replace(order, side=Side.SELL if side is Side.BUY else Side.BUY)
    assert attempt(opposite, bar).fill is not None
