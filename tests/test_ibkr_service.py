from datetime import UTC, datetime
from types import SimpleNamespace

from ib_async import Contract, Order, OrderStatus, Trade
import pytest

from northstar_quant.config.instrument_registry import (
    InstrumentDefinition,
    InstrumentRegistry,
)
from northstar_quant.execution import ibkr_service


class _FakeIB:
    def accountSummary(self, account: str):
        assert account == "DU123456"
        return [
            SimpleNamespace(
                account="DU123456",
                tag="NetLiquidation",
                value="100000",
                currency="USD",
            ),
            SimpleNamespace(
                account="DU-OTHER",
                tag="NetLiquidation",
                value="999999",
                currency="USD",
            ),
        ]

    def positions(self, account: str):
        assert account == "DU123456"
        return [
            SimpleNamespace(
                account="DU123456",
                contract=SimpleNamespace(symbol="SPY", conId=756733),
                position=10,
                avgCost=500,
                marketPrice=510,
            ),
            SimpleNamespace(
                account="DU-OTHER",
                contract=SimpleNamespace(symbol="QQQ", conId=320227571),
                position=20,
                avgCost=400,
                marketPrice=410,
            ),
        ]

    def reqAllOpenOrders(self):
        return [
            SimpleNamespace(
                contract=SimpleNamespace(symbol="SPY", conId=756733),
                order=SimpleNamespace(
                    orderId=11,
                    account="DU123456",
                    action="BUY",
                    totalQuantity=1,
                    clientId=7,
                    permId=101,
                    orderRef="NSQ-ref",
                ),
                orderStatus=SimpleNamespace(
                    filled=0,
                    remaining=1,
                    avgFillPrice=0,
                    status="Submitted",
                ),
            ),
            SimpleNamespace(
                contract=SimpleNamespace(symbol="QQQ", conId=320227571),
                order=SimpleNamespace(
                    orderId=12,
                    account="DU-OTHER",
                    action="BUY",
                    totalQuantity=1,
                    clientId=8,
                    permId=102,
                    orderRef="other",
                ),
                orderStatus=SimpleNamespace(
                    filled=0,
                    remaining=1,
                    avgFillPrice=0,
                    status="Submitted",
                ),
            ),
        ]

    def reqCompletedOrders(self, apiOnly: bool):
        assert apiOnly is True
        return [
            SimpleNamespace(
                contract=SimpleNamespace(symbol="SPY", conId=756733),
                order=SimpleNamespace(
                    orderId=10,
                    account="DU123456",
                    action="BUY",
                    totalQuantity=2,
                    clientId=7,
                    permId=100,
                    orderRef="NSQ-completed",
                    orderType="LMT",
                    lmtPrice=505,
                ),
                orderStatus=SimpleNamespace(
                    filled=2,
                    remaining=0,
                    avgFillPrice=505,
                    status="Filled",
                ),
                orderState=SimpleNamespace(
                    completedTime="20260723 02:00:00 UTC",
                    completedStatus="Filled",
                ),
            ),
            SimpleNamespace(
                contract=SimpleNamespace(symbol="QQQ", conId=320227571),
                order=SimpleNamespace(
                    orderId=9,
                    account="DU-OTHER",
                    action="SELL",
                    totalQuantity=1,
                    clientId=8,
                    permId=99,
                    orderRef="other-completed",
                    orderType="MKT",
                    lmtPrice=0,
                ),
                orderStatus=SimpleNamespace(
                    filled=0,
                    remaining=1,
                    avgFillPrice=0,
                    status="Cancelled",
                ),
                orderState=SimpleNamespace(
                    completedTime="20260723 01:00:00 UTC",
                    completedStatus="Cancelled",
                ),
            ),
        ]

    def trades(self):
        return [
            SimpleNamespace(
                contract=SimpleNamespace(symbol="SPY", conId=756733),
                order=SimpleNamespace(
                    orderId=10,
                    account="DU123456",
                    action="BUY",
                    totalQuantity=2,
                    clientId=8,
                    permId=888,
                    orderRef="other-client",
                ),
                orderStatus=SimpleNamespace(
                    filled=0,
                    remaining=2,
                    avgFillPrice=0,
                    status="Submitted",
                ),
            )
        ]

    def fills(self):
        return [
            SimpleNamespace(
                contract=SimpleNamespace(symbol="SPY", conId=756733),
                execution=SimpleNamespace(
                    orderId=10,
                    shares=2,
                    price=505,
                    side="BOT",
                    time=datetime(2026, 7, 23, 2, 0, tzinfo=UTC),
                    acctNumber="DU123456",
                    execId="exec-1",
                    permId=100,
                    clientId=7,
                    orderRef="NSQ-completed",
                ),
            ),
            SimpleNamespace(
                contract=SimpleNamespace(symbol="QQQ", conId=320227571),
                execution=SimpleNamespace(
                    orderId=9,
                    shares=1,
                    price=400,
                    side="SLD",
                    time=datetime(2026, 7, 23, 1, 0, tzinfo=UTC),
                    acctNumber="DU-OTHER",
                    execId="exec-other",
                    permId=99,
                    clientId=8,
                ),
            ),
        ]


def _registry() -> InstrumentRegistry:
    return InstrumentRegistry(
        broker="ibkr",
        version=1,
        instruments=(
            InstrumentDefinition(
                data_symbol="SPY.DATA",
                broker_symbol="SPY",
                con_id=756733,
                sec_type="STK",
                exchange="SMART",
                primary_exchange="ARCA",
                currency="USD",
                enabled=True,
            ),
        ),
    )


def _real_ib_async_completed_trade() -> Trade:
    """复刻 ib_async Wrapper.completedOrder 实际返回的 Trade 形状。"""

    return Trade(
        contract=Contract(
            secType="STK",
            conId=756733,
            symbol="SPY",
            exchange="SMART",
            currency="USD",
        ),
        order=Order(
            account="DU123456",
            action="BUY",
            totalQuantity=2,
            orderType="LMT",
            lmtPrice=505,
            orderRef="NSQ-real-completed",
            permId=1001,
            filledQuantity=2,
        ),
        # completedOrder 消息没有 orderId/clientId；ib_async wrapper 只把
        # OrderState.status 搬进 OrderStatus，其余字段保留 dataclass 缺省值。
        orderStatus=OrderStatus(status="Filled"),
    )


def test_ibkr_state_is_filtered_to_target_account_and_normalizes_fill_side(
    monkeypatch,
):
    settings = SimpleNamespace(
        ibkr_account="DU123456",
        ibkr_client_id=7,
        trading_currency="USD",
    )
    monkeypatch.setattr(ibkr_service, "get_settings", lambda: settings)
    service = ibkr_service.IBKRService(instrument_registry=_registry())
    service._ib = _FakeIB()
    service._connected = True

    state = service.sync_state()

    assert state.account == "DU123456"
    assert state.state_complete is True
    assert state.account_values["NetLiquidation"] == "100000"
    assert [(item.symbol, item.account, item.con_id) for item in state.positions] == [
        ("SPY.DATA", "DU123456", 756733)
    ]
    assert [row["broker_order_id"] for row in state.open_orders] == ["11"]
    assert state.open_orders[0]["symbol"] == "SPY.DATA"
    assert state.open_orders[0]["client_id"] == 7
    assert [row["broker_order_id"] for row in state.completed_orders] == ["10"]
    assert state.completed_orders[0] == {
        "broker_order_id": "10",
        "account": "DU123456",
        "client_id": 7,
        "perm_id": 100,
        "order_ref": "NSQ-completed",
        "con_id": 756733,
        "symbol": "SPY.DATA",
        "side": "BUY",
        "qty": 2.0,
        "filled_qty": 2.0,
        "remaining_qty": 0.0,
        "avg_fill_price": None,
        "status": "Filled",
        "order_type": "LMT",
        "limit_price": 505.0,
        "completed_at": "20260723 02:00:00 UTC",
    }
    assert [
        (item.symbol, item.side, item.account, item.exec_id, item.order_ref)
        for item in state.fills
    ] == [
        ("SPY.DATA", "BUY", "DU123456", "exec-1", "NSQ-completed")
    ]


def test_ibkr_completed_orders_parse_real_ib_async_trade_shape(monkeypatch):
    class _CompletedIB:
        def reqCompletedOrders(self, apiOnly: bool):
            assert apiOnly is True
            return [_real_ib_async_completed_trade()]

    settings = SimpleNamespace(
        ibkr_account="DU123456",
        ibkr_client_id=7,
        trading_currency="USD",
    )
    monkeypatch.setattr(ibkr_service, "get_settings", lambda: settings)
    service = ibkr_service.IBKRService(instrument_registry=_registry())
    service._ib = _CompletedIB()
    service._connected = True

    row = service.completed_orders()[0]

    assert row["broker_order_id"] is None
    assert row["client_id"] is None
    assert row["order_ref"] == "NSQ-real-completed"
    assert row["perm_id"] == 1001
    assert row["con_id"] == 756733
    assert row["filled_qty"] == 2.0
    assert row["remaining_qty"] == 0.0
    assert row["status"] == "Filled"
    assert row["completed_at"] is None


def test_ibkr_order_status_recovers_completed_order_by_stable_identity(
    monkeypatch,
):
    class _CompletedIB:
        def trades(self):
            return []

        def reqCompletedOrders(self, apiOnly: bool):
            assert apiOnly is True
            return [_real_ib_async_completed_trade()]

    settings = SimpleNamespace(
        ibkr_account="DU123456",
        ibkr_client_id=7,
        trading_currency="USD",
    )
    monkeypatch.setattr(ibkr_service, "get_settings", lambda: settings)
    service = ibkr_service.IBKRService(instrument_registry=_registry())
    service._ib = _CompletedIB()
    service._connected = True

    assert service.order_status("10") is None
    row = service.order_status(
        "10",
        expected_order_ref="NSQ-real-completed",
        expected_client_id=7,
        expected_con_id=756733,
    )

    assert row is not None
    assert row["broker_order_id"] is None
    assert row["client_id"] is None
    assert row["perm_id"] == 1001
    assert row["filled_qty"] == 2.0
    with pytest.raises(RuntimeError, match="conId"):
        service.order_status(
            "10",
            expected_order_ref="NSQ-real-completed",
            expected_perm_id=1001,
            expected_client_id=7,
            expected_con_id=999999,
        )


def test_ibkr_order_status_falls_back_to_completed_orders(monkeypatch):
    settings = SimpleNamespace(
        ibkr_account="DU123456",
        ibkr_client_id=7,
        trading_currency="USD",
    )
    monkeypatch.setattr(ibkr_service, "get_settings", lambda: settings)
    service = ibkr_service.IBKRService(instrument_registry=_registry())
    service._ib = _FakeIB()
    service._connected = True

    row = service.order_status("10")

    assert row is not None
    assert row["broker_order_id"] == "10"
    assert row["account"] == "DU123456"
    assert row["status"] == "Filled"
    assert row["filled_qty"] == 2.0
    assert row["remaining_qty"] == 0.0
    with pytest.raises(RuntimeError, match="orderRef"):
        service.order_status(
            "10",
            expected_order_ref="NSQ-wrong-completed",
            expected_client_id=7,
        )


def test_ibkr_quotes_use_registry_contract_and_return_data_symbol(monkeypatch):
    class _QuoteIB:
        def reqMarketDataType(self, _market_data_type: int) -> None:
            return None

        def reqTickers(self, *contracts):
            return [
                SimpleNamespace(
                    contract=contract,
                    bid=500.0,
                    ask=501.0,
                    last=500.5,
                    close=499.0,
                    marketDataType=1,
                    marketPrice=lambda: 500.5,
                )
                for contract in contracts
            ]

    settings = SimpleNamespace(
        ibkr_account="DU123456",
        ibkr_client_id=7,
        trading_currency="USD",
    )
    monkeypatch.setattr(ibkr_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        ibkr_service,
        "qualify_ibkr_contract",
        lambda _ib, instrument: SimpleNamespace(
            conId=instrument.con_id,
            symbol=instrument.broker_symbol,
        ),
    )
    service = ibkr_service.IBKRService(instrument_registry=_registry())
    service._ib = _QuoteIB()
    service._connected = True

    quotes = service.snapshot_quotes(["SPY.DATA"])

    assert len(quotes) == 1
    assert quotes[0].symbol == "SPY.DATA"
    assert quotes[0].market_price == 500.5


def test_ibkr_cancel_uses_account_client_id_order_id_and_order_ref(monkeypatch):
    own_order = SimpleNamespace(
        orderId=42,
        account="DU123456",
        clientId=7,
        orderRef="NSQ-own-order",
    )
    other_client_order = SimpleNamespace(
        orderId=42,
        account="DU123456",
        clientId=8,
        orderRef="other-client",
    )

    class _CancelIB:
        def __init__(self) -> None:
            self.cancelled = []

        def reqAllOpenOrders(self):
            return [
                SimpleNamespace(order=other_client_order),
                SimpleNamespace(order=own_order),
            ]

        def cancelOrder(self, order) -> None:
            self.cancelled.append(order)

        def sleep(self, _seconds: float) -> None:
            return None

    settings = SimpleNamespace(
        ibkr_account="DU123456",
        ibkr_client_id=7,
        trading_currency="USD",
    )
    monkeypatch.setattr(ibkr_service, "get_settings", lambda: settings)
    fake_ib = _CancelIB()
    service = ibkr_service.IBKRService(instrument_registry=_registry())
    service._ib = fake_ib
    service._connected = True

    assert service.cancel_order("42") is True
    assert fake_ib.cancelled == [own_order]
    with pytest.raises(RuntimeError, match="orderRef"):
        service.cancel_order(
            "42",
            expected_order_ref="NSQ-different-order",
            expected_client_id=7,
        )
    assert fake_ib.cancelled == [own_order]


def test_ibkr_account_summary_rejects_missing_account_identity(monkeypatch):
    class _AccountIB:
        def accountSummary(self, _account: str):
            return [
                SimpleNamespace(
                    account="",
                    tag="NetLiquidation",
                    value="100000",
                    currency="USD",
                )
            ]

    settings = SimpleNamespace(
        ibkr_account="DU123456",
        ibkr_client_id=7,
        trading_currency="USD",
    )
    monkeypatch.setattr(ibkr_service, "get_settings", lambda: settings)
    service = ibkr_service.IBKRService(instrument_registry=_registry())
    service._ib = _AccountIB()
    service._connected = True

    try:
        service.account_values()
    except RuntimeError as exc:
        assert "缺少账户标识" in str(exc)
    else:
        raise AssertionError("缺少账户标识的摘要必须 fail closed")
