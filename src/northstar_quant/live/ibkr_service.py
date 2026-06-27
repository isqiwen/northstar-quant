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
import math
from typing import Any
from uuid import uuid4

from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.config.settings import get_settings
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    MarketQuoteSnapshot,
    PositionSnapshot,
)

try:
    from ib_async import IB, Stock
except Exception:  # pragma: no cover
    IB = None
    Stock = None


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

    def positions(
        self,
        *,
        snapshot_asof: datetime | None = None,
        snapshot_batch_id: str | None = None,
    ) -> list[PositionSnapshot]:
        """拉取真实持仓。"""

        self.connect()
        batch_asof = ensure_utc(snapshot_asof)
        batch_id = snapshot_batch_id or f"ibkr-pos-{uuid4().hex}"
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
                    asof=batch_asof,
                    snapshot_batch_id=batch_id,
                )
            )
        return snapshots

    @staticmethod
    def _safe_price(value: Any) -> float | None:
        try:
            price = float(value)
        except Exception:
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        return price

    def _request_snapshot_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
        if not symbols:
            return []
        if Stock is None:
            raise RuntimeError("未安装 ib_async，无法拉取 IBKR 行情快照。")

        contracts = [
            Stock(symbol, "SMART", self.settings.trading_currency)
            for symbol in symbols
        ]
        qualified_contracts = self.ib.qualifyContracts(*contracts)
        if not qualified_contracts:
            return []

        tickers = self.ib.reqTickers(*qualified_contracts)
        quote_asof = utc_now()
        quotes: list[MarketQuoteSnapshot] = []
        for ticker in tickers:
            contract = getattr(ticker, "contract", None)
            market_price_attr = getattr(ticker, "marketPrice", None)
            market_price = (
                market_price_attr()
                if callable(market_price_attr)
                else market_price_attr
            )
            market_data_type = (
                int(getattr(ticker, "marketDataType", 0) or 0)
                if getattr(ticker, "marketDataType", None) is not None
                else None
            )
            quotes.append(
                MarketQuoteSnapshot(
                    symbol=str(getattr(contract, "symbol", "") or ""),
                    bid=self._safe_price(getattr(ticker, "bid", None)),
                    ask=self._safe_price(getattr(ticker, "ask", None)),
                    last=self._safe_price(getattr(ticker, "last", None)),
                    close=self._safe_price(getattr(ticker, "close", None)),
                    market_price=self._safe_price(market_price),
                    market_data_type=market_data_type,
                    asof=quote_asof,
                    source=(
                        "broker_snapshot_delayed"
                        if market_data_type in {3, 4}
                        else "broker_snapshot"
                    ),
                )
            )
        return quotes

    def snapshot_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
        """拉取一次性市场报价快照。

        优先尝试 live market data；若缺失，再补一次 delayed snapshot。
        """

        self.connect()
        normalized_symbols = sorted(
            {
                str(symbol).strip().upper()
                for symbol in symbols
                if str(symbol).strip()
            }
        )
        if not normalized_symbols:
            return []

        with suppress(Exception):
            self.ib.reqMarketDataType(1)
        live_quotes = self._request_snapshot_quotes(normalized_symbols)
        quote_by_symbol = {
            quote.symbol.upper(): quote
            for quote in live_quotes
            if str(quote.symbol).strip()
        }

        missing_symbols = [
            symbol
            for symbol in normalized_symbols
            if symbol not in quote_by_symbol
            or (
                quote_by_symbol[symbol].bid is None
                and quote_by_symbol[symbol].ask is None
                and quote_by_symbol[symbol].last is None
                and quote_by_symbol[symbol].market_price is None
                and quote_by_symbol[symbol].close is None
            )
        ]
        if missing_symbols:
            with suppress(Exception):
                self.ib.reqMarketDataType(3)
            delayed_quotes = self._request_snapshot_quotes(missing_symbols)
            for quote in delayed_quotes:
                quote_by_symbol[str(quote.symbol).strip().upper()] = quote
            with suppress(Exception):
                self.ib.reqMarketDataType(1)

        return [quote_by_symbol[symbol] for symbol in normalized_symbols if symbol in quote_by_symbol]

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

        snapshot_asof = utc_now()
        snapshot_batch_id = f"ibkr-pos-{uuid4().hex}"
        return BrokerStateSnapshot(
            positions=self.positions(
                snapshot_asof=snapshot_asof,
                snapshot_batch_id=snapshot_batch_id,
            ),
            open_orders=self.open_orders(),
            fills=self.recent_fills(),
            account_values=self.account_values(),
            asof=snapshot_asof,
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
