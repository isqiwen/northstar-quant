from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from northstar_quant.accounting.fifo import Account, FillFact
from northstar_quant.execution.orders import Offset, PendingOrder, Side, intraday_offset
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
    assert simulate_fill(order, bar, market, fee_per_lot=Decimal(2), slippage_ticks=1) is None
    fact = simulate_fill(order, bar, market, fee_per_lot=Decimal(2), slippage_ticks=0)
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
    )
    later = MarketBar(
        UUID(int=12),
        at + timedelta(minutes=1),
        at + timedelta(minutes=2),
        at + timedelta(minutes=2, seconds=1),
        date(2026, 1, 5),
        Decimal(105),
        Decimal(100),
    )
    closing_fact = simulate_fill(closing, later, market, fee_per_lot=Decimal(2), slippage_ticks=1)
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
    assert intraday_offset(1, Side.SELL, 1) is Offset.CLOSE_TODAY
    with pytest.raises(ValueError, match="cross through zero"):
        intraday_offset(1, Side.SELL, 2)


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
