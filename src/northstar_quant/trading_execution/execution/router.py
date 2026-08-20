"""订单路由模块。"""

from __future__ import annotations

from collections.abc import Callable

from northstar_quant.trading_execution.broker.broker_base import BrokerAdapter
from northstar_quant.trading_execution.execution.models import OrderRequest, OrderResult
from northstar_quant.platform.observability.logging.logger import get_logger
from northstar_quant.portfolio_risk.limits.models import OrderRiskContext, RiskLimits
from northstar_quant.portfolio_risk.risk.pretrade import reserve_order_context, validate_order

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
        submission_guard: Callable[[OrderRequest], None] | None = None,
    ):
        self.broker = broker
        self.limits = limits
        self.risk_context = risk_context
        self.submission_guard = submission_guard

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
        prepared_order = self.broker.prepare_order(order)
        # instrument 解析后再次校验最终载荷；submission guard 也必须看到实际
        # 将持久化并送往券商的订单，而不是解析前的临时对象。
        validate_order(prepared_order, self.limits, self.risk_context)
        if self.submission_guard is not None:
            self.submission_guard(prepared_order)
        result = self.broker.submit_order(prepared_order)
        if result.accepted and not result.replayed:
            reserve_order_context(self.risk_context, prepared_order)
        route_logger.info("订单路由完成，status=%s", result.status)
        return result
