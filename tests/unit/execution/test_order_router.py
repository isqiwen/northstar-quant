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


def test_order_router_rechecks_submission_guard_immediately_before_broker_call():
    broker = _AcceptingBroker()
    guard_enabled = True

    def submission_guard(_order: OrderRequest) -> None:
        if guard_enabled:
            raise PermissionError("kill switch 已开启")

    router = OrderRouter(
        broker,
        RiskLimits(max_order_notional=None),
        submission_guard=submission_guard,
    )

    with pytest.raises(PermissionError, match="kill switch"):
        router.route(
            OrderRequest(
                strategy_id="test",
                symbol="510300.SS",
                side="BUY",
                qty=1.0,
                reference_price=100.0,
            )
        )

    assert broker.orders == []


def test_order_router_does_not_reserve_risk_capacity_for_idempotent_replay():
    class _ReplayBroker(_AcceptingBroker):
        def submit_order(self, order: OrderRequest) -> OrderResult:
            self.orders.append(order)
            return OrderResult(
                accepted=True,
                broker_order_id="existing-1",
                status="Submitted",
                replayed=True,
            )

    broker = _ReplayBroker()
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

    assert context.reserved_buy_notional == 0.0


def test_long_only_router_rejects_opening_short_position():
    broker = _AcceptingBroker()
    router = OrderRouter(
        broker,
        RiskLimits(max_order_notional=None, long_only=True),
        risk_context=OrderRiskContext(position_qty_by_symbol={}),
    )

    with pytest.raises(ValueError, match="超过可卖持仓"):
        router.route(
            OrderRequest(
                strategy_id="test",
                symbol="RB2405",
                side="SELL",
                qty=1.0,
                reference_price=100.0,
            )
        )

    assert broker.orders == []


def test_non_long_only_router_allows_short_when_sellable_check_is_disabled():
    broker = _AcceptingBroker()
    router = OrderRouter(
        broker,
        RiskLimits(
            max_order_notional=None,
            long_only=False,
            enforce_sellable_qty=False,
        ),
        risk_context=OrderRiskContext(position_qty_by_symbol={}),
    )

    result = router.route(
        OrderRequest(
            strategy_id="test",
            symbol="RB2405",
            side="SELL",
            qty=1.0,
            reference_price=100.0,
        )
    )

    assert result.accepted is True
    assert len(broker.orders) == 1
