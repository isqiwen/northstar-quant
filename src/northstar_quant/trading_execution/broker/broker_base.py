"""券商适配器抽象层。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from northstar_quant.trading_execution.broker.contracts import (
    BrokerCapabilities,
    BrokerConnectionState,
    BrokerIdentity,
    BrokerMode,
    BrokerStatus,
    MarketGateway,
)
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    MarketQuoteSnapshot,
    OrderRequest,
    OrderResult,
)


class BrokerAdapter(ABC):
    """所有券商适配器都要遵循的统一接口。"""

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> OrderResult:
        """提交一笔订单。"""

    def prepare_order(self, order: OrderRequest) -> OrderRequest:
        """补充实际券商载荷所需的稳定身份。

        默认保持订单不变；需要 instrument 映射的适配器应在这里完成纯本地解析，
        让上层能够在券商调用前持久化最终载荷。
        """

        return order

    def restore_order_attempt(self, order: OrderRequest) -> OrderRequest:
        """恢复同一次执行尝试已经持久化的载荷。

        默认没有持久化能力，保持订单不变。追价执行器会在重新计算价格后调用
        此方法，让具备持久化能力的适配器复用崩溃前的数量和价格。
        """

        return order

    def get_order_attempt_state(self, order: OrderRequest) -> dict | None:
        """读取同一次执行尝试的持久化状态与券商身份。

        默认适配器没有持久化账本，返回 ``None``。具备持久化能力的包装器应
        返回该 attempt 的 ``orderRef/clientId/permId/instrumentId`` 以及成交进度，
        供重连后的追价逻辑精确匹配券商快照。
        """

        del order
        return None

    def list_order_plan_attempts(self, order: OrderRequest) -> list[dict]:
        """列出同一执行 plan 已持久化的全部 attempt。

        默认适配器没有持久化账本，返回空列表。追价执行器会在任何路由前用
        这些记录校验当前 ``max_steps`` 与 fallback 配置是否仍兼容。
        """

        del order
        return []

    @abstractmethod
    def get_name(self) -> str:
        """返回券商适配器名称。"""

    def connect(self) -> None:
        """建立连接。

        对于短连接适配器，这里可以什么都不做；
        对于长连接适配器，这里应真正建立连接。
        """

    def disconnect(self) -> None:
        """断开连接。"""

    def sync_state(self) -> BrokerStateSnapshot:
        """同步券商状态。

        默认返回空快照，纸面券商或未实现适配器可以直接复用。
        """

        return BrokerStateSnapshot()

    def get_market_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
        """读取给定标的的市场报价快照。

        默认返回空列表，由调用方决定是否回退到本地估值价格。
        """

        del symbols
        return []

    def cancel_order(self, broker_order_id: str) -> bool:
        """撤销一笔订单。

        返回 True 表示适配器已接受撤单请求；
        返回 False 表示当前适配器不支持或撤单失败。
        """

        return False

    def cancel_order_with_identity(
        self,
        broker_order_id: str,
        *,
        order_ref: str | None = None,
        perm_id: int | None = None,
        client_id: int | None = None,
        instrument_id: str | None = None,
        exchange_id: str | None = None,
    ) -> bool:
        """带完整券商身份撤单；默认适配器退化为普通撤单。"""

        del order_ref, perm_id, client_id, instrument_id, exchange_id
        return self.cancel_order(broker_order_id)

    def cancel_order_for_local_order(
        self,
        local_order_id: int,
        broker_order_id: str,
    ) -> bool:
        """按本地订单主键撤单；持久化包装器应覆盖此方法。"""

        del local_order_id
        return self.cancel_order(broker_order_id)

    def get_order_status(self, broker_order_id: str) -> dict | None:
        """查询单笔订单状态。

        追价执行器必须依赖可确认的终态才能重试。适配器不支持该能力时返回
        ``None``，调用方应停止追价，而不是假设订单已经撤销。
        """

        del broker_order_id
        return None

    def get_order_status_with_identity(
        self,
        broker_order_id: str,
        *,
        order_ref: str | None = None,
        perm_id: int | None = None,
        client_id: int | None = None,
        instrument_id: str | None = None,
        exchange_id: str | None = None,
    ) -> dict | None:
        """使用完整券商身份查询状态；默认退化为普通查询。"""

        del order_ref, perm_id, client_id, instrument_id, exchange_id
        return self.get_order_status(broker_order_id)

    def get_account(self) -> str | None:
        """返回该适配器当前明确绑定的账户。"""

        return None

    def get_client_id(self) -> int | None:
        """返回券商 API client 身份；不适用的适配器返回 ``None``。"""

        return None

    def market_gateway(self) -> MarketGateway:
        """Expose the adapter's read-only quote boundary explicitly."""

        return self

    def broker_status(self) -> BrokerStatus:
        """Default is deliberately UNKNOWN: it can never authorize new risk."""

        return BrokerStatus(
            identity=BrokerIdentity(self.get_name(), BrokerMode.PAPER, self.get_account(), self.get_client_id()),
            connection_state=BrokerConnectionState.UNKNOWN,
            capabilities=BrokerCapabilities(False, False, False, False, False),
            observed_at=datetime.now(UTC),
        )
