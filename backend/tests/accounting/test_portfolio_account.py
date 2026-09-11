"""One currency/cash ledger, isolated FIFO inventory and complete portfolio marks."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from northstar_quant.accounting.fifo import Account, FillFact
from northstar_quant.accounting.portfolio import value_portfolio, value_single_contract
from northstar_quant.accounting.settlement import SettlementFact
from northstar_quant.execution.orders import Offset, Side
from northstar_quant.market_data import Market
from tests.accounting.test_terms import terms

AT = datetime(2026, 1, 5, 1, tzinfo=UTC)
A = Market(UUID(int=1), "SYNTHETIC_A", "Asia/Shanghai", "CNY", "LOT", Decimal(1), Decimal(10), 60)
B = replace(A, contract_id=UUID(int=2), symbol="SYNTHETIC_B", multiplier=Decimal(5))


def fills():
    return (
        FillFact(
            "a",
            "order-a",
            A.contract_id,
            None,
            AT,
            AT.date(),
            Side.BUY,
            Offset.OPEN,
            2,
            Decimal(100),
            Decimal(2),
        ),
        FillFact(
            "b",
            "order-b",
            B.contract_id,
            None,
            AT,
            AT.date(),
            Side.SELL,
            Offset.OPEN,
            3,
            Decimal(200),
            Decimal(3),
        ),
    )


def test_one_cash_balance_marks_and_margin_cover_every_open_contract():
    account = Account(Decimal(10000), (B, A))
    for fill in fills():
        account.apply(fill)
    marks = {A.contract_id: Decimal(110), B.contract_id: Decimal(190)}
    effective = {
        A.contract_id: terms(),
        B.contract_id: replace(terms(), contract_id=B.contract_id, upper_limit=Decimal(400)),
    }
    value = value_portfolio(account, marks, at=AT, terms=effective)
    assert value.cash == 9995 and value.total_fees == 5
    assert value.unrealized_pnl == 350 and value.equity == 10345
    assert value.net_exposure == -650 and value.gross_exposure == 5050
    assert value.margin_used == 813
    assert value.to_dict()["available"] == "9532"
    assert [h.position.net_lots for h in value.holdings] == [2, -3]
    with pytest.raises(ValueError, match="explicit mark"):
        value_portfolio(account, {A.contract_id: marks[A.contract_id]}, at=AT)
    with pytest.raises(ValueError, match="margin terms"):
        value_portfolio(account, marks, at=AT, terms={A.contract_id: effective[A.contract_id]})
    with pytest.raises(ValueError, match="omit"):
        value_single_contract(account, Decimal(110), at=AT)
    with pytest.raises(ValueError, match="currency"):
        Account(Decimal(10000), (A, replace(B, currency="USD")))
    before = account.checkpoint()
    assert account.apply(fills()[0]) == account.applied_fills[0]
    assert account.checkpoint() == before
    with pytest.raises(ValueError, match="reused"):
        account.apply(replace(fills()[0], contract_id=B.contract_id))
    with pytest.raises(RuntimeError, match="fault"):
        with account.transaction():
            account.apply(replace(fills()[0], fill_id="extra-a"))
            account.apply(replace(fills()[1], fill_id="extra-b"))
            raise RuntimeError("cross-contract event fault")
    assert account.checkpoint() == before


def test_contract_settlement_does_not_roll_other_inventory_or_duplicate_cash():
    account = Account(Decimal(10000), (A, B))
    first, second = fills()
    account.apply(first)
    account.apply(second)
    closed = replace(
        second,
        fill_id="b-close",
        side=Side.BUY,
        offset=Offset.CLOSE_TODAY,
        quantity_lots=1,
        price=Decimal(190),
        fee=Decimal(1),
        filled_at=AT + timedelta(seconds=2),
    )
    account.apply(closed)
    settle = SettlementFact(
        "settle-a",
        A.contract_id,
        AT.date(),
        AT.date() + timedelta(days=1),
        AT + timedelta(seconds=1),
        AT + timedelta(seconds=3),
        Decimal(110),
        "synthetic delayed A settlement",
    )
    marks = {A.contract_id: Decimal(110), B.contract_id: Decimal(190)}
    equity = account.equity(marks)
    account.settle(settle, at=settle.available_at)
    assert account.equity(marks) == equity == 10344
    assert account.cash == 10244 and account.settlement_pnl == 200
    assert account.position(A.contract_id).long_yesterday == 2
    assert account.position(B.contract_id).short_today == 2
    assert account.position(B.contract_id).short_yesterday == 0
    before = account.checkpoint()
    account.settle(settle, at=settle.available_at)
    assert account.checkpoint() == before
    with pytest.raises(ValueError, match="settlement"):
        account.apply(
            replace(
                second,
                fill_id="b-next",
                trading_day=settle.next_trading_day,
                filled_at=settle.available_at + timedelta(seconds=1),
            )
        )
    assert account.checkpoint() == before
    rebuilt = Account(account.initial_cash, account.markets)
    for fact in (first, second, closed):
        rebuilt.apply(fact)
    rebuilt.settle(settle, at=settle.available_at)
    assert rebuilt.checkpoint() == before
