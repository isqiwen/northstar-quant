"""Daily variation preserves equity, age-specific closing and replay identities."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, Inexact, localcontext
from uuid import uuid4

import pytest

from northstar_quant.accounting.fifo import Account
from northstar_quant.accounting.fills import FillFact
from northstar_quant.accounting.settlement import SettlementFact
from northstar_quant.execution.orders import Offset, Side
from northstar_quant.market_data import Instrument

DAY = date(2026, 1, 9)
NEXT = date(2026, 1, 12)
OPEN = datetime(2026, 1, 9, 1, tzinfo=UTC)
CLOSE = datetime(2026, 1, 9, 7, tzinfo=UTC)


def _account():
    return Account(
        Decimal(10000),
        (Instrument(uuid4(), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10)),),
    )


def _fill(account, identity, side, offset, lots, price, *, day=DAY, at=OPEN):
    return FillFact(
        identity,
        identity,
        account.markets[0].contract_id,
        None,
        at,
        day,
        side,
        offset,
        lots,
        Decimal(price),
        Decimal(2),
        available_at=at,
    )


def _settlement(account):
    return SettlementFact(
        "settlement-friday",
        account.markets[0].contract_id,
        DAY,
        NEXT,
        CLOSE,
        CLOSE + timedelta(minutes=10),
        Decimal(110),
        "synthetic identified settlement for engineering acceptance",
    )


def test_variation_does_not_close_positions_or_double_count_pnl():
    account = _account()
    opening = _fill(account, "long", Side.BUY, Offset.OPEN, 2, 100)
    account.apply(opening)
    account.apply(_fill(account, "short", Side.SELL, Offset.OPEN, 1, 120))
    fact = _settlement(account)
    equity = account.equity({account.markets[0].contract_id: fact.price})
    applied = account.settle(fact, at=fact.available_at)
    assert applied.variation_pnl == Decimal(300)
    assert account.equity({account.markets[0].contract_id: fact.price}) == equity == Decimal(10296)
    assert account.unrealized_pnl({account.markets[0].contract_id: fact.price}) == 0
    assert account.fill_count == 2
    assert (
        account.position(account.markets[0].contract_id).long_yesterday == 2
        and account.position(account.markets[0].contract_id).short_yesterday == 1
    )
    assert (
        account.position(account.markets[0].contract_id).long_today
        == account.position(account.markets[0].contract_id).short_today
        == 0
    )
    assert (
        account.apply(opening).fact == opening
    )  # delivery retry after settlement is still a no-op
    assert account.settle(fact, at=fact.available_at) == applied
    closed = account.apply(
        _fill(
            account,
            "close-old",
            Side.SELL,
            Offset.CLOSE_YESTERDAY,
            1,
            115,
            day=NEXT,
            at=CLOSE + timedelta(hours=6),
        )
    )
    assert closed.realized_pnl == Decimal(50)  # only movement since settlement
    assert account.realized_pnl == Decimal(350)
    assert account.cash == Decimal(10344)


def test_today_close_cannot_consume_yesterday_cost_lots():
    account = _account()
    account.apply(_fill(account, "old", Side.BUY, Offset.OPEN, 1, 100))
    fact = _settlement(account)
    account.settle(fact, at=fact.available_at)
    at = CLOSE + timedelta(hours=6)
    account.apply(_fill(account, "new", Side.BUY, Offset.OPEN, 1, 120, day=NEXT, at=at))
    applied = account.apply(
        _fill(account, "close-new", Side.SELL, Offset.CLOSE_TODAY, 1, 125, day=NEXT, at=at)
    )
    assert applied.realized_pnl == 50
    assert (
        account.position(account.markets[0].contract_id).long_yesterday == 1
        and account.position(account.markets[0].contract_id).long_today == 0
    )
    assert account.unrealized_pnl({account.markets[0].contract_id: Decimal(125)}) == 150
    before = account.checkpoint()
    with pytest.raises(ValueError):
        account.apply(
            _fill(account, "extra", Side.SELL, Offset.CLOSE_TODAY, 1, 125, day=NEXT, at=at)
        )
    assert account.checkpoint() == before


def test_settlement_requires_available_evidence_and_rejects_conflicting_identity():
    account = _account()
    account.apply(_fill(account, "open", Side.BUY, Offset.OPEN, 1, 100))
    fact = _settlement(account)
    before = account.checkpoint()
    with pytest.raises(ValueError, match="not yet available"):
        account.settle(fact, at=CLOSE)
    assert account.checkpoint() == before
    account.settle(fact, at=fact.available_at)
    settled = account.checkpoint()
    with pytest.raises(ValueError, match="identity"):
        account.settle(replace(fact, price=Decimal(111)), at=fact.available_at)
    with pytest.raises(ValueError, match="trading day"):
        account.settle(replace(fact, settlement_id="duplicate-day"), at=fact.available_at)
    assert account.checkpoint() == settled


def test_failed_transaction_removes_variation_and_fact_identity_then_replay_matches():
    account = _account()
    opening = _fill(account, "open", Side.BUY, Offset.OPEN, 2, 100)
    account.apply(opening)
    fact = _settlement(account)
    before = account.checkpoint()
    with pytest.raises(RuntimeError, match="next component"):
        with account.transaction():
            account.settle(fact, at=fact.available_at)
            raise RuntimeError("next component failed")
    assert account.checkpoint() == before
    account.settle(fact, at=fact.available_at)
    restored = Account(account.initial_cash, (account.markets[0],))
    restored.apply(FillFact.from_dict(opening.to_dict()))
    replay = SettlementFact.from_dict(fact.to_dict())
    restored.settle(replay, at=replay.available_at)
    assert restored.checkpoint() == account.checkpoint()


def test_settlement_pnl_is_exact_under_hostile_decimal_context():
    account = _account()
    account.apply(_fill(account, "open", Side.BUY, Offset.OPEN, 12345, "123456789.123456789"))
    fact = replace(_settlement(account), price=Decimal("123456790.123456789"))
    with localcontext() as context:
        context.prec = 4
        context.traps[Inexact] = True
        applied = account.settle(fact, at=fact.available_at)
    assert applied.variation_pnl == Decimal(123450)
    assert account.unrealized_pnl({account.markets[0].contract_id: fact.price}) == 0
