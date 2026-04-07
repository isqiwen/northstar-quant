"""IBKR 长连接服务。

本模块把 IBKR 连接管理从下单适配器中抽离出来，专门负责：
1. 建立 / 复用与 TWS 或 IB Gateway 的长连接
2. 拉取真实持仓、未完成订单、账户净值等状态
3. 作为订单/成交对账的数据来源
4. 作为订单状态轮询与成交回写的数据来源

对日频 ETF 系统来说，长连接不是为了追求毫秒级速度，而是为了：
- 减少频繁连断带来的不稳定
- 统一持仓同步、订单同步、成交同步入口
- 方便以后扩展心跳、重连、健康检查
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from typing import Any

from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.config.settings import get_settings
from northstar_quant.execution.models import BrokerStateSnapshot, FillSnapshot, PositionSnapshot

try:
    from ib_async import IB
except Exception:  # pragma: no cover
    IB = None


class IBKRService:
    """IBKR 长连接服务。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._ib = None
        self._connected = False

    @property
    def ib(self):
        if self._ib is None:
            if IB is None:
                raise RuntimeError("未安装 ib_async，无法创建 IBKR 连接。")
            self._ib = IB()
        return self._ib

    def connect(self) -> None:
        """建立到 TWS / Gateway 的长连接。"""

        if self._connected:
            return
        self.ib.connect(
            host=self.settings.ibkr_host,
            port=self.settings.ibkr_port,
            clientId=self.settings.ibkr_client_id,
            readonly=self.settings.ibkr_readonly,
        )
        self._connected = True

    def disconnect(self) -> None:
        """断开连接。"""

        if self._ib is not None and self._connected:
            with suppress(Exception):
                self._ib.disconnect()
        self._connected = False

    def is_connected(self) -> bool:
        """返回当前连接状态。"""

        return bool(self._connected)

    def account_values(self) -> dict[str, Any]:
        """拉取账户关键信息。"""

        self.connect()
        data: dict[str, Any] = {}
        for item in self.ib.accountSummary():
            key = getattr(item, 'tag', '')
            value = getattr(item, 'value', None)
            if key:
                data[key] = value
        return data

    def positions(self) -> list[PositionSnapshot]:
        """拉取真实持仓。"""

        self.connect()
        snapshots: list[PositionSnapshot] = []
        for pos in self.ib.positions():
            contract = getattr(pos, 'contract', None)
            market_price = float(getattr(pos, 'marketPrice', 0.0) or 0.0)
            qty = float(getattr(pos, 'position', 0.0))
            snapshots.append(
                PositionSnapshot(
                    symbol=getattr(contract, 'symbol', ''),
                    qty=qty,
                    avg_cost=float(getattr(pos, 'avgCost', 0.0) or 0.0),
                    market_price=market_price,
                    market_value=qty * market_price if market_price else None,
                    account=getattr(pos, 'account', None),
                    asof=utc_now(),
                )
            )
        return snapshots

    def open_orders(self) -> list[dict]:
        """拉取未完成订单与状态。"""

        self.connect()
        rows: list[dict] = []
        for trade in self.ib.openTrades():
            contract = getattr(trade, 'contract', None)
            order = getattr(trade, 'order', None)
            order_status = getattr(trade, 'orderStatus', None)
            rows.append(
                {
                    'broker_order_id': str(getattr(order, 'orderId', '')),
                    'symbol': getattr(contract, 'symbol', ''),
                    'side': getattr(order, 'action', ''),
                    'qty': float(getattr(order, 'totalQuantity', 0.0) or 0.0),
                    'filled_qty': float(getattr(order_status, 'filled', 0.0) or 0.0),
                    'remaining_qty': float(getattr(order_status, 'remaining', 0.0) or 0.0),
                    'avg_fill_price': float(getattr(order_status, 'avgFillPrice', 0.0) or 0.0),
                    'status': getattr(order_status, 'status', None) or 'open',
                }
            )
        return rows

    def recent_fills(self) -> list[FillSnapshot]:
        """拉取近期成交。"""

        self.connect()
        rows: list[FillSnapshot] = []
        for fill in self.ib.fills():
            execution = getattr(fill, 'execution', None)
            contract = getattr(fill, 'contract', None)
            time_value = getattr(execution, 'time', None)
            rows.append(
                FillSnapshot(
                    broker_order_id=str(getattr(execution, 'orderId', '')),
                    symbol=getattr(contract, 'symbol', ''),
                    qty=float(getattr(execution, 'shares', 0.0) or 0.0),
                    price=float(getattr(execution, 'price', 0.0) or 0.0),
                    side=str(getattr(execution, 'side', '')),
                    filled_at=ensure_utc(time_value if isinstance(time_value, datetime) else None),
                    account=self.settings.ibkr_account,
                )
            )
        return rows

    def sync_state(self) -> BrokerStateSnapshot:
        """一次性同步券商全量状态。"""

        return BrokerStateSnapshot(
            positions=self.positions(),
            open_orders=self.open_orders(),
            fills=self.recent_fills(),
            account_values=self.account_values(),
            asof=utc_now(),
        )


    def cancel_order(self, broker_order_id: str) -> bool:
        """按 broker_order_id 撤单。

        这里通过当前 openTrades 搜索对应订单，再调用 IBKR 撤单。
        对个人日频系统来说，这种实现已经足够清晰可维护。
        """

        self.connect()
        target = str(broker_order_id)
        for trade in self.ib.openTrades():
            order = getattr(trade, 'order', None)
            oid = str(getattr(order, 'orderId', ''))
            if oid != target:
                continue
            self.ib.cancelOrder(order)
            with suppress(Exception):
                self.ib.sleep(0.5)
            return True
        return False
