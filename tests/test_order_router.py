import pytest

from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.models import OrderRequest, OrderResult
from northstar_quant.execution.router import OrderRouter
from northstar_quant.risk.models import OrderRiskContext, RiskLimits


class _AcceptingBroker(BrokerAdapter):
    def __init__(self) -> None:
        self.orders: list[OrderRequest] = []

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self.orders.append(order)
        return OrderResult(
            accepted=True,
            broker_order_id=f"accepted-{len(self.orders)}",
            status="Submitted",
        )

    def get_name(self) -> str:
        return "fake"


def test_order_router_reserves_available_cash_for_order_batch():
    broker = _AcceptingBroker()
    context = OrderRiskContext(available_cash=1000.0)
    router = OrderRouter(
        broker,
        RiskLimits(max_order_notional=None),
        risk_context=context,
    )

    router.route(
        OrderRequest(
            strategy_id="test",
            symbol="510300.SS",
            side="BUY",
            qty=5.0,
            reference_price=100.0,
        )
    )

    with pytest.raises(ValueError, match="买入订单金额超过可用资金"):
        router.route(
            OrderRequest(
                strategy_id="test",
                symbol="510500.SS",
                side="BUY",
                qty=6.0,
                reference_price=100.0,
            )
        )

    assert len(broker.orders) == 1
    assert context.reserved_buy_notional == 500.0


def test_order_router_reserves_sellable_position_for_order_batch():
    broker = _AcceptingBroker()
    context = OrderRiskContext(
        position_qty_by_symbol={
            "510300.SS": 100.0,
        }
    )
    router = OrderRouter(
        broker,
        RiskLimits(max_order_notional=None),
        risk_context=context,
    )

    router.route(
        OrderRequest(
            strategy_id="test",
            symbol="510300.SS",
            side="SELL",
            qty=60.0,
            reference_price=50.0,
        )
    )

    with pytest.raises(ValueError, match="卖出订单数量超过可卖持仓"):
        router.route(
            OrderRequest(
                strategy_id="test",
                symbol="510300.SS",
                side="SELL",
                qty=50.0,
                reference_price=50.0,
            )
        )

    assert len(broker.orders) == 1
    assert context.reserved_sell_qty_by_symbol["510300.SS"] == 60.0
