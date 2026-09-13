"""Cash movements are exact, idempotent facts and never strategy profit."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from northstar_quant.accounting.cashflows import CashFlowFact
from northstar_quant.accounting.fifo import Account
from northstar_quant.market_data import Instrument

AT = datetime(2026, 9, 7, 1, tzinfo=UTC)
MARKET = Instrument(UUID(int=1), "RB2610", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10))


def flow(identity="deposit", amount="100.123456789012345678", **changes):
    return replace(
        CashFlowFact(identity, Decimal(amount), "CNY", AT, AT, "confirmed-transfer"), **changes
    )


def test_cash_flow_dedup_reversal_replay_and_rollback_preserve_exact_equity():
    account = Account(Decimal(1000), (MARKET,))
    deposit = flow()
    withdrawal = flow("withdraw", "-1200", available_at=AT + timedelta(seconds=1))
    with localcontext() as context:
        context.prec = 6
        account.transfer(deposit)
        account.transfer(withdrawal)
        assert account.cash == Decimal("-99.876543210987654322")
    assert account.realized_pnl == account.total_fees == account.settlement_pnl == 0
    assert account.transfer(deposit) == deposit
    before = account.checkpoint()
    with pytest.raises(ValueError, match="identity"):
        account.transfer(replace(deposit, amount=Decimal(200)))
    assert account.checkpoint() == before
    reversal = flow(
        "reversal", "1200", reverses_id="withdraw", available_at=AT + timedelta(seconds=2)
    )
    with pytest.raises(RuntimeError):
        with account.transaction():
            account.transfer(reversal)
            raise RuntimeError("transaction interrupted")
    assert account.checkpoint() == before
    account.transfer(reversal)
    assert account.cash == Decimal("1100.123456789012345678")
    with pytest.raises(ValueError, match="unreversed"):
        account.transfer(replace(reversal, cash_flow_id="second-reversal"))
    rebuilt = Account(Decimal(1000), (MARKET,))
    for fact in (deposit, withdrawal, reversal):
        rebuilt.transfer(CashFlowFact.from_dict(fact.to_dict()))
    assert rebuilt.checkpoint() == account.checkpoint()
    assert account.equity({MARKET.contract_id: Decimal(3000)}) == account.cash


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "USD"},
        {"available_at": AT - timedelta(seconds=1)},
        {"reverses_id": "missing"},
    ],
)
def test_invalid_cash_movement_cannot_modify_account(changes):
    account = Account(Decimal(1000), (MARKET,))
    before = account.checkpoint()
    with pytest.raises(ValueError):
        account.transfer(flow(**changes))
    assert account.checkpoint() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"AccountID": "another"},
        {"ErrorID": 1},
        {"FutureSerial": 0},
        {"CustFee": "1"},
        {"TransferStatus": "1"},
    ],
)
def test_unconfirmed_native_transfer_never_becomes_a_cash_fact(changes):
    from northstar_quant.broker.transfers import decode_transfer

    event = {
        "channel": "TD",
        "callback": "OnRtnFromBankToFutureByBank",
        "error_id": 0,
        "received_at": (AT + timedelta(seconds=2)).isoformat(),
        "data": {
            "BrokerID": "9999",
            "AccountID": "123456",
            "TradingDay": "20260907",
            "TradeDate": "20260907",
            "TradeTime": "09:00:01",
            "CurrencyID": "CNY",
            "FutureSerial": 1,
            "TradeAmount": "500",
            "CustFee": "0",
            "BrokerFee": "0",
            "TransferStatus": "0",
            "ErrorID": 0,
        },
    }
    args = {
        "broker_id": "9999",
        "account_id": "123456",
        "trading_day": "20260907",
        "opening_at": AT,
        "source_reference": "synthetic-native-receipt",
    }
    assert decode_transfer(event, **args).amount == Decimal(500)
    event["data"].update(changes)
    with pytest.raises(ValueError):
        decode_transfer(event, **args)


def test_transfer_fill_fee_and_cross_day_settlement_share_one_cash_equation():
    from northstar_quant.accounting.fees import FeeFact
    from northstar_quant.accounting.fills import FillFact
    from northstar_quant.accounting.settlement import SettlementFact
    from northstar_quant.execution.orders import Offset, Side

    account = Account(Decimal(1000), (MARKET,))
    account.transfer(flow(amount="500"))
    fill = FillFact(
        "open",
        "order",
        MARKET.contract_id,
        None,
        AT,
        AT.date(),
        Side.BUY,
        Offset.OPEN,
        2,
        Decimal(100),
        None,
        available_at=AT,
    )
    account.apply(fill)
    assert account.checkpoint()["cash"] is None
    fee = FeeFact("fee", ("open",), Decimal(3), "CNY", AT, AT, "confirmed-charge")
    account.confirm_fee(fee)
    assert account.cash == Decimal(1497)
    settlement = SettlementFact(
        "close",
        MARKET.contract_id,
        AT.date(),
        AT.date() + timedelta(days=1),
        AT + timedelta(hours=6),
        AT + timedelta(hours=7),
        Decimal(110),
        "confirmed-settlement",
    )
    account.settle(settlement, at=settlement.available_at)
    assert account.cash == Decimal(1697)
    assert account.settlement_pnl == account.realized_pnl == Decimal(200)
    assert account.net_cash_flow == Decimal(500)
    assert account.position(MARKET.contract_id).long_yesterday == 2
    closing_at = AT + timedelta(days=1)
    account.apply(
        replace(
            fill,
            fill_id="close",
            order_id="close-order",
            filled_at=closing_at,
            trading_day=closing_at.date(),
            side=Side.SELL,
            offset=Offset.CLOSE_YESTERDAY,
            price=Decimal(115),
            fee=Decimal(2),
            available_at=closing_at,
        )
    )
    assert account.cash == Decimal(1795)
    assert (
        account.cash
        == account.initial_cash + account.net_cash_flow + account.realized_pnl - account.total_fees
    )
