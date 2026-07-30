"""持久化报单测试使用的构造器与 broker fake。"""

from northstar_quant.common.time import utc_now
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.models import OrderRequest, OrderResult


def order_request(**overrides) -> OrderRequest:
    values = {
        "strategy_id": "core",
        "symbol": "RB2405",
        "side": "BUY",
        "qty": 2.0,
        "account": "DU123456",
        "order_type": "LMT",
        "limit_price": 500.0,
        "run_id": "run-1",
        "batch_id": "batch-1",
        "plan_id": "plan-1",
        "attempt_no": 1,
    }
    values.update(overrides)
    return OrderRequest(**values)


class RecordingBroker(BrokerAdapter):
    def __init__(
        self,
        *,
        on_submit=None,
        on_cancel=None,
        error: Exception | None = None,
        cancel_error: Exception | None = None,
    ) -> None:
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.error = error
        self.cancel_error = cancel_error
        self.submit_count = 0
        self.cancel_count = 0

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self.submit_count += 1
        if self.on_submit is not None:
            self.on_submit(order)
        if self.error is not None:
            raise self.error
        return OrderResult(
            accepted=True,
            broker_order_id="broker-42",
            status="Submitted",
            submitted_at=utc_now(),
        )

    def get_name(self) -> str:
        return "ctp"

    def get_account(self) -> str:
        return "DU123456"

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancel_count += 1
        if self.on_cancel is not None:
            self.on_cancel(broker_order_id)
        if self.cancel_error is not None:
            raise self.cancel_error
        return True
