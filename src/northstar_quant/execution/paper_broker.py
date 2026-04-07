"""纸面券商适配器。"""

from __future__ import annotations

from uuid import uuid4

from northstar_quant.common.time import utc_now
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.models import BrokerStateSnapshot, OrderRequest, OrderResult


class PaperBrokerAdapter(BrokerAdapter):
    """纸面交易券商。"""

    def __init__(self) -> None:
        self.accepted_orders: list[dict] = []

    def submit_order(self, order: OrderRequest) -> OrderResult:
        broker_order_id = f"paper-{uuid4().hex[:12]}"
        self.accepted_orders.append(
            {
                "broker_order_id": broker_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "order_semantic": order.order_semantic,
                "submitted_at": utc_now(),
            }
        )
        return OrderResult(
            accepted=True,
            broker_order_id=broker_order_id,
            status="accepted",
            message=f"纸面订单已接受：{order.symbol} {order.side} {order.qty}",
            submitted_at=utc_now(),
        )

    def sync_state(self) -> BrokerStateSnapshot:
        return BrokerStateSnapshot(open_orders=list(self.accepted_orders), asof=utc_now())

    def get_name(self) -> str:
        return "paper"
