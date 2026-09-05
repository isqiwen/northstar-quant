from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from northstar_quant.accounting import Account, FillFact
from northstar_quant.data.research import Market, ResearchBar
from northstar_quant.execution import PendingOrder, simulate_fill
from northstar_quant.risk import Side


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
        2,
        Decimal(100),
        Decimal(102),
    )
    bar = ResearchBar(
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
        2,
        Decimal(100),
        Decimal(110),
    )
    later = ResearchBar(
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
        quantity_lots=1,
        price=Decimal(120),
        fee=Decimal(1),
    )
    assert account.apply(partial_close).realized_pnl == Decimal(200)
    reversal = replace(
        partial_close, fill_id="fill-4", quantity_lots=3, price=Decimal(90), fee=Decimal(3)
    )
    assert account.apply(reversal).realized_pnl == Decimal(-300)
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
