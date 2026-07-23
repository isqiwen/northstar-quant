from types import SimpleNamespace

import pytest

from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.limit_chase_executor import LimitChaseExecutor
from northstar_quant.execution.models import OrderRequest, OrderResult
from northstar_quant.risk.models import OrderRiskContext, RiskLimits


class _RecordingBroker(BrokerAdapter):
    def __init__(self, *, cancel_result: bool = True) -> None:
        self.cancel_result = cancel_result
        self.orders: list[OrderRequest] = []
        self.cancel_requests: list[str] = []

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self.orders.append(order)
        return OrderResult(
            accepted=True,
            broker_order_id=f"order-{len(self.orders)}",
            status="Submitted",
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancel_requests.append(broker_order_id)
        return self.cancel_result

    def get_name(self) -> str:
        return "fake"


class _ScriptedChaseExecutor(LimitChaseExecutor):
    def __init__(self, *args, resolutions: list[dict | None], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.resolutions = list(resolutions)

    def _wait_for_terminal_or_timeout(
        self,
        broker_order_id: str,
        *,
        expected_qty: float,
    ) -> dict | None:
        del broker_order_id, expected_qty
        return self.resolutions.pop(0)


def _settings(*, max_steps: int = 3, fallback_mode: str = "cancel"):
    return SimpleNamespace(
        limit_chase_max_steps=max_steps,
        limit_chase_fallback_mode=fallback_mode,
        limit_chase_per_step_timeout_seconds=1,
        limit_chase_sleep_seconds=0.2,
    )


def _base_order() -> OrderRequest:
    return OrderRequest(
        strategy_id="test",
        symbol="510300.SS",
        side="BUY",
        qty=10.0,
        reference_price=100.0,
        planned_trade_value=1000.0,
    )


def test_limit_chase_resubmits_only_remaining_qty_after_confirmed_partial_cancel():
    broker = _RecordingBroker()
    context = OrderRiskContext(available_cash=2000.0)
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        risk_context=context,
        resolutions=[
            None,
            {"status": "Cancelled", "filled_qty": 4.0},
            {"status": "Filled", "filled_qty": 6.0},
        ],
    )
    executor.settings = _settings(max_steps=2)

    result = executor.execute(_base_order(), reference_price=100.0)

    assert [order.qty for order in broker.orders] == [10.0, 6.0]
    assert broker.cancel_requests == ["order-1"]
    assert result.final_mode == "limit_filled"
    # 资金预留按每轮实际限价计价，不能退回较低的原始计划金额。
    assert context.reserved_buy_notional == pytest.approx(1002.4)


def test_limit_chase_stops_when_cancel_is_not_confirmed():
    broker = _RecordingBroker()
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        resolutions=[None, None],
    )
    executor.settings = _settings()

    result = executor.execute(_base_order(), reference_price=100.0)

    assert len(broker.orders) == 1
    assert broker.cancel_requests == ["order-1"]
    assert result.final_mode == "uncertain_stop"
    assert result.final_result.status == "cancel_unconfirmed"


def test_limit_chase_stops_on_unknown_terminal_without_retry_or_market_fallback():
    broker = _RecordingBroker()
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        resolutions=[{"status": "UnknownTerminal", "filled_qty": 2.0}],
    )
    executor.settings = _settings(fallback_mode="market")

    result = executor.execute(_base_order(), reference_price=100.0)

    assert len(broker.orders) == 1
    assert broker.cancel_requests == []
    assert result.final_mode == "uncertain_stop"
    assert result.final_result.status == "unknown_terminal"


def test_limit_chase_market_fallback_uses_only_confirmed_remaining_qty():
    broker = _RecordingBroker()
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        resolutions=[{"status": "Cancelled", "filled_qty": 4.0}],
    )
    executor.settings = _settings(max_steps=1, fallback_mode="market")

    result = executor.execute(_base_order(), reference_price=100.0)

    assert [(order.order_type, order.qty) for order in broker.orders] == [
        ("LMT", 10.0),
        ("MKT", 6.0),
    ]
    assert result.final_mode == "fallback_market"
