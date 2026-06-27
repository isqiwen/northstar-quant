"""订单路由模块。"""

from __future__ import annotations

from northstar_quant.logging_.logger import get_logger
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.models import OrderRequest, OrderResult
from northstar_quant.risk.models import OrderRiskContext, RiskLimits
from northstar_quant.risk.pretrade import reserve_order_context, validate_order

logger = get_logger(__name__)


class OrderRouter:
    """统一订单路由入口。

    职责只有两件事：
    1. 在下单前执行统一的交易前风控
    2. 把合格订单发送给具体券商适配器
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        limits: RiskLimits,
        risk_context: OrderRiskContext | None = None,
    ):
        self.broker = broker
        self.limits = limits
        self.risk_context = risk_context

    def route(self, order: OrderRequest) -> OrderResult:
        """执行交易前风控，并发送订单。"""

        route_logger = logger.bind(
            command="order.route",
            strategy=order.strategy_id,
            symbol=order.symbol,
            order_semantic=order.order_semantic,
            broker=self.broker.get_name(),
        )
        route_logger.info("开始执行订单路由")
        validate_order(order, self.limits, self.risk_context)
        result = self.broker.submit_order(order)
        if result.accepted:
            reserve_order_context(self.risk_context, order)
        route_logger.info("订单路由完成，status=%s", result.status)
        return result
