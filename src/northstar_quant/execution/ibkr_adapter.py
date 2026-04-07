"""IBKR 实盘适配器。"""

from __future__ import annotations

from northstar_quant.common.time import utc_now
from northstar_quant.config.settings import get_settings
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.models import BrokerStateSnapshot, OrderRequest, OrderResult
from northstar_quant.live.ibkr_service import IBKRService
from northstar_quant.logging_.logger import get_logger

try:
    from ib_async import LimitOrder, MarketOrder, Stock
except Exception:  # pragma: no cover
    LimitOrder = None
    MarketOrder = None
    Stock = None

logger = get_logger(__name__, broker="ibkr")


class IBKRBrokerAdapter(BrokerAdapter):
    """IBKR 券商适配器。

    这一层只关心“把统一订单模型翻译成 IBKR 订单”，
    持仓同步、账户状态、长连接重连等能力交给 IBKRService 处理。
    """

    def __init__(self, service: IBKRService | None = None) -> None:
        self.settings = get_settings()
        self.service = service or IBKRService()

    def connect(self) -> None:
        logger.bind(command="broker.connect").info("开始连接 IBKR")
        self.service.connect()

    def disconnect(self) -> None:
        logger.bind(command="broker.disconnect").info("断开 IBKR 连接")
        self.service.disconnect()

    def submit_order(self, order: OrderRequest) -> OrderResult:
        order_logger = logger.bind(
            command="broker.submit-order",
            strategy=order.strategy_id,
            symbol=order.symbol,
            order_semantic=order.order_semantic,
        )
        if Stock is None or MarketOrder is None:
            raise RuntimeError("未安装 ib_async，无法发送 IBKR 订单。")

        self.service.connect()
        ib = self.service.ib
        contract = Stock(order.symbol, "SMART", self.settings.trading_currency)
        ib.qualifyContracts(contract)

        if order.order_type.upper() == "LMT":
            if order.limit_price is None:
                raise ValueError("限价单必须提供 limit_price")
            ib_order = LimitOrder(order.side.upper(), order.qty, order.limit_price)
        else:
            ib_order = MarketOrder(order.side.upper(), order.qty)

        if order.account:
            setattr(ib_order, 'account', order.account)
        elif self.settings.ibkr_account:
            setattr(ib_order, 'account', self.settings.ibkr_account)

        trade = ib.placeOrder(contract, ib_order)
        ib.sleep(1.0)

        order_id = str(getattr(trade.order, 'orderId', ''))
        status = getattr(getattr(trade, 'orderStatus', None), 'status', 'submitted')
        order_logger.info("IBKR 订单已提交，status=%s，broker_order_id=%s", status, order_id)

        return OrderResult(
            accepted=True,
            broker_order_id=order_id or f"ibkr-{order.symbol}-{int(utc_now().timestamp())}",
            status=status,
            message=f"IBKR 订单已提交：{order.symbol} {order.side} {order.qty}",
            submitted_at=utc_now(),
        )

    def sync_state(self) -> BrokerStateSnapshot:
        logger.bind(command="broker.sync-state").info("开始同步 IBKR 状态")
        return self.service.sync_state()

    def cancel_order(self, broker_order_id: str) -> bool:
        """向 IBKR 发送撤单请求。"""

        self.service.connect()
        canceled = self.service.cancel_order(broker_order_id)
        logger.bind(command="broker.cancel-order", broker_order_id=broker_order_id).info(
            "IBKR 撤单请求已发送，canceled=%s",
            canceled,
        )
        return canceled

    def get_name(self) -> str:
        return "ibkr"
