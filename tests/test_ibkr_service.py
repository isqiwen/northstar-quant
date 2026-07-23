from datetime import UTC, datetime
from types import SimpleNamespace

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


def test_ibkr_state_is_filtered_to_target_account_and_normalizes_fill_side(
    monkeypatch,
):
    settings = SimpleNamespace(
        ibkr_account="DU123456",
        trading_currency="USD",
    )
    monkeypatch.setattr(ibkr_service, "get_settings", lambda: settings)
    service = ibkr_service.IBKRService()
    service._ib = _FakeIB()
    service._connected = True

    state = service.sync_state()

    assert state.account == "DU123456"
    assert state.state_complete is True
    assert state.account_values["NetLiquidation"] == "100000"
    assert [(item.symbol, item.account, item.con_id) for item in state.positions] == [
        ("SPY", "DU123456", 756733)
    ]
    assert [row["broker_order_id"] for row in state.open_orders] == ["11"]
    assert state.open_orders[0]["client_id"] == 7
    assert [(item.side, item.account, item.exec_id) for item in state.fills] == [
        ("BUY", "DU123456", "exec-1")
    ]
