"""券商适配器抽象层。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    OrderRequest,
    OrderResult,
)


class BrokerAdapter(ABC):
    """所有券商适配器都要遵循的统一接口。"""

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> OrderResult:
        """提交一笔订单。"""

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

    def cancel_order(self, broker_order_id: str) -> bool:
        """撤销一笔订单。

        返回 True 表示适配器已接受撤单请求；
        返回 False 表示当前适配器不支持或撤单失败。
        """

        return False
