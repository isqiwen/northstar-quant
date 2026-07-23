"""IBKR 长连接与状态采集服务。

该服务属于券商基础设施，由执行适配器和上层实盘编排共同使用，专门负责：
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
import sys
from typing import Any
from uuid import uuid4

from northstar_quant.common.order_status import is_final_order_status
from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.config.instrument_registry import (
    InstrumentDefinition,
    InstrumentRegistry,
    load_instrument_registry,
)
from northstar_quant.config.settings import get_settings
from northstar_quant.execution.ibkr_contracts import qualify_ibkr_contract
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    MarketQuoteSnapshot,
    PositionSnapshot,
)

IB: Any
try:
    from ib_async import IB as _IB
except Exception:  # pragma: no cover
    IB = None
else:
    IB = _IB


class IBKRService:
    """IBKR 长连接服务。"""

    def __init__(
        self,
        *,
        instrument_registry: InstrumentRegistry | None = None,
    ) -> None:
        self.settings = get_settings()
        self._ib = None
        self._connected = False
        self._instrument_registry = instrument_registry

    def _registry(self) -> InstrumentRegistry:
        """读取当前正式 instrument registry。"""

        return self._instrument_registry or load_instrument_registry()

    def _data_symbol(self, contract: Any) -> str:
        """用券商 conId 反解数据 symbol；无法证明身份时 fail closed。"""

        con_id = int(getattr(contract, "conId", 0) or 0)
        if con_id <= 0:
            raise RuntimeError("IBKR 合约缺少有效 conId，无法映射数据 symbol。")
        return self._registry().resolve_con_id(con_id).data_symbol

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

    def _target_account(self) -> str:
        """返回必须显式配置的目标账户。"""

        account = str(self.settings.ibkr_account or "").strip()
        if not account:
            raise RuntimeError("未显式配置 NORTHSTAR_IBKR_ACCOUNT，禁止读取混合账户状态。")
        return account

    def account_values(self) -> dict[str, Any]:
        """拉取账户关键信息。"""

        self.connect()
        target_account = self._target_account()
        data: dict[str, Any] = {}
        matched_count = 0
        for item in self.ib.accountSummary(target_account):
            item_account = str(getattr(item, "account", "") or "").strip()
            if not item_account:
                raise RuntimeError(
                    "IBKR 账户摘要缺少账户标识，无法证明资金状态归属。"
                )
            if item_account != target_account:
                continue
            matched_count += 1
            key = str(getattr(item, "tag", "") or "").strip()
            value = getattr(item, "value", None)
            currency = str(getattr(item, "currency", "") or "").strip().upper()
            if key and (
                key not in data
                or currency in {"", "BASE", self.settings.trading_currency.upper()}
            ):
                data[key] = value
        if matched_count == 0:
            raise RuntimeError(f"IBKR 未返回目标账户 {target_account} 的账户状态。")
        return data

    def positions(
        self,
        *,
        snapshot_asof: datetime | None = None,
        snapshot_batch_id: str | None = None,
    ) -> list[PositionSnapshot]:
        """拉取真实持仓。"""

        self.connect()
        target_account = self._target_account()
        batch_asof = ensure_utc(snapshot_asof)
        batch_id = snapshot_batch_id or f"ibkr-pos-{uuid4().hex}"
        snapshots: list[PositionSnapshot] = []
        for pos in self.ib.positions(target_account):
            position_account = str(getattr(pos, "account", "") or "").strip()
            if position_account != target_account:
                continue
            contract = getattr(pos, 'contract', None)
            market_price = float(getattr(pos, 'marketPrice', 0.0) or 0.0)
            qty = float(getattr(pos, 'position', 0.0))
            snapshots.append(
                PositionSnapshot(
                    symbol=self._data_symbol(contract),
                    qty=qty,
                    avg_cost=float(getattr(pos, 'avgCost', 0.0) or 0.0),
                    market_price=market_price,
                    market_value=qty * market_price if market_price else None,
                    account=position_account,
                    con_id=int(getattr(contract, "conId", 0) or 0) or None,
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

    @staticmethod
    def _optional_quantity(value: Any) -> float | None:
        """解析 IBKR 可选数量，并识别 ib_async 的 UNSET_DOUBLE。"""

        if value is None:
            return None
        try:
            quantity = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"IBKR 返回了无效订单数量：{value!r}") from exc
        if quantity == sys.float_info.max:
            return None
        if not math.isfinite(quantity) or quantity < 0:
            raise RuntimeError(f"IBKR 返回了无效订单数量：{value!r}")
        return quantity

    def _request_snapshot_quotes(
        self,
        instruments: tuple[InstrumentDefinition, ...],
    ) -> list[MarketQuoteSnapshot]:
        if not instruments:
            return []
        qualified_contracts = [
            qualify_ibkr_contract(self.ib, instrument)
            for instrument in instruments
        ]
        data_symbol_by_con_id = {
            instrument.con_id: instrument.data_symbol
            for instrument in instruments
        }

        tickers = self.ib.reqTickers(*qualified_contracts)
        quote_asof = utc_now()
        quotes: list[MarketQuoteSnapshot] = []
        for ticker in tickers:
            contract = getattr(ticker, "contract", None)
            con_id = int(getattr(contract, "conId", 0) or 0)
            data_symbol = data_symbol_by_con_id.get(con_id)
            if data_symbol is None:
                raise RuntimeError(
                    "IBKR 行情返回了 instrument registry 之外的 conId，"
                    f"conId={con_id}。"
                )
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
                    symbol=data_symbol,
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

    def snapshot_quotes(
        self,
        symbols: list[str],
        *,
        instrument_registry: InstrumentRegistry | None = None,
    ) -> list[MarketQuoteSnapshot]:
        """拉取一次性市场报价快照。

        所有 symbol 必须先通过正式 instrument registry 解析。优先尝试 live
        market data；若缺失，再补一次 delayed snapshot。
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
        registry = instrument_registry or self._registry()
        instruments = registry.resolve_many(normalized_symbols)
        instrument_by_symbol = {
            instrument.data_symbol: instrument
            for instrument in instruments
        }

        with suppress(Exception):
            self.ib.reqMarketDataType(1)
        live_quotes = self._request_snapshot_quotes(instruments)
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
            delayed_quotes = self._request_snapshot_quotes(
                tuple(instrument_by_symbol[symbol] for symbol in missing_symbols)
            )
            for quote in delayed_quotes:
                quote_by_symbol[str(quote.symbol).strip().upper()] = quote
            with suppress(Exception):
                self.ib.reqMarketDataType(1)

        return [quote_by_symbol[symbol] for symbol in normalized_symbols if symbol in quote_by_symbol]

    def open_orders(self) -> list[dict]:
        """拉取所有 API client 的未完成订单，并限定到目标账户。"""

        self.connect()
        target_account = self._target_account()
        trades = self.ib.reqAllOpenOrders()
        return self._order_rows(
            trades,
            target_account=target_account,
            state_name="未完成订单",
        )

    def completed_orders(self) -> list[dict]:
        """拉取 API 提交的已完成订单，并限定到目标账户。"""

        self.connect()
        target_account = self._target_account()
        trades = self.ib.reqCompletedOrders(apiOnly=True)
        return self._order_rows(
            trades,
            target_account=target_account,
            state_name="已完成订单",
            require_final_status=True,
        )

    def _order_rows(
        self,
        trades: list[Any],
        *,
        target_account: str,
        state_name: str,
        require_final_status: bool = False,
    ) -> list[dict]:
        """把 IBKR Trade 统一为可持久化的订单状态行。"""

        rows: list[dict] = []
        for trade in trades:
            contract = getattr(trade, "contract", None)
            order = getattr(trade, "order", None)
            order_status = getattr(trade, "orderStatus", None)
            order_state = getattr(trade, "orderState", None)
            order_account = str(getattr(order, "account", "") or "").strip()
            if not order_account:
                raise RuntimeError(
                    f"IBKR {state_name}缺少账户标识，无法证明账户状态完整。"
                )
            if order_account != target_account:
                continue
            status = str(getattr(order_status, "status", "") or "").strip()
            if not status:
                status = str(
                    getattr(order_state, "completedStatus", "") or ""
                ).strip()
            completed_at = str(
                getattr(order_state, "completedTime", "") or ""
            ).strip()
            raw_order_id = getattr(order, "orderId", None)
            if raw_order_id is None or str(raw_order_id).strip() == "":
                raw_order_id = getattr(order_status, "orderId", None)
            normalized_order_id = (
                str(raw_order_id).strip()
                if raw_order_id is not None
                else ""
            )
            broker_order_id = normalized_order_id or None
            if require_final_status and normalized_order_id == "0":
                # reqCompletedOrders 的协议消息不含 orderId；ib_async 会留下
                # Order / OrderStatus 的 dataclass 缺省值 0，不能当成真实身份。
                broker_order_id = None
            if broker_order_id is None and not require_final_status:
                raise RuntimeError(
                    f"IBKR {state_name}缺少 broker_order_id，无法安全恢复订单状态。"
                )
            if require_final_status and not is_final_order_status(status):
                order_label = broker_order_id or str(
                    getattr(order, "orderRef", "") or "N/A"
                )
                raise RuntimeError(
                    f"IBKR 已完成订单 {order_label} 返回非终态 "
                    f"{status or 'N/A'}，已停止状态恢复。"
                )
            raw_client_id = getattr(order, "clientId", None)
            if raw_client_id is None:
                raw_client_id = getattr(order_status, "clientId", None)
            client_id = (
                int(raw_client_id)
                if raw_client_id is not None
                and str(raw_client_id).strip()
                else None
            )
            if require_final_status and client_id == 0:
                # completedOrder 消息同样不含 clientId，0 只是 dataclass 缺省值。
                client_id = None
            perm_id = int(
                getattr(order, "permId", 0)
                or getattr(order_status, "permId", 0)
                or 0
            )
            order_ref = str(getattr(order, "orderRef", "") or "").strip() or None
            con_id = int(getattr(contract, "conId", 0) or 0) or None
            if (
                require_final_status
                and (broker_order_id is None or client_id is None)
                and (order_ref is None or perm_id <= 0 or con_id is None)
            ):
                raise RuntimeError(
                    "IBKR 已完成订单缺少 orderId/clientId，且没有完整的 "
                    "orderRef/permId/conId 强身份，已停止状态恢复。"
                )
            qty = float(getattr(order, "totalQuantity", 0.0) or 0.0)
            filled_qty = float(
                getattr(order_status, "filled", 0.0) or 0.0
            )
            remaining_qty = float(
                getattr(order_status, "remaining", 0.0) or 0.0
            )
            if require_final_status:
                # ib_async 的 completedOrder wrapper 只在 OrderStatus 写入状态；
                # completed 消息的真实累计成交量位于 Order.filledQuantity。
                completed_filled_qty = self._optional_quantity(
                    getattr(order, "filledQuantity", None)
                )
                if completed_filled_qty is not None:
                    filled_qty = completed_filled_qty
                    remaining_qty = max(qty - filled_qty, 0.0)
            rows.append(
                {
                    "broker_order_id": broker_order_id,
                    "account": order_account,
                    "client_id": client_id,
                    "perm_id": perm_id or None,
                    "order_ref": order_ref,
                    "con_id": con_id,
                    "symbol": self._data_symbol(contract),
                    "side": str(getattr(order, "action", "") or ""),
                    "qty": qty,
                    "filled_qty": filled_qty,
                    "remaining_qty": remaining_qty,
                    # completedOrder 协议不提供真实 avgFillPrice；ib_async
                    # OrderStatus 的 0 只是缺省值，不能覆盖已持久化均价。
                    "avg_fill_price": (
                        None
                        if require_final_status
                        else float(
                            getattr(order_status, "avgFillPrice", 0.0)
                            or 0.0
                        )
                    ),
                    "status": status or "open",
                    "order_type": str(
                        getattr(order, "orderType", "") or ""
                    ) or None,
                    "limit_price": IBKRService._safe_price(
                        getattr(order, "lmtPrice", None)
                    ),
                    "completed_at": completed_at or None,
                }
            )
        return rows

    def recent_fills(self) -> list[FillSnapshot]:
        """拉取目标账户的近期成交，并统一券商方向编码。"""

        self.connect()
        target_account = self._target_account()
        rows: list[FillSnapshot] = []
        for fill in self.ib.fills():
            execution = getattr(fill, 'execution', None)
            contract = getattr(fill, 'contract', None)
            fill_account = str(
                getattr(execution, "acctNumber", "") or ""
            ).strip()
            if not fill_account:
                raise RuntimeError("IBKR 成交缺少账户标识，无法证明账户状态完整。")
            if fill_account != target_account:
                continue
            raw_side = str(getattr(execution, "side", "") or "").strip().upper()
            try:
                side = {
                    "BOT": "BUY",
                    "BUY": "BUY",
                    "SLD": "SELL",
                    "SELL": "SELL",
                }[raw_side]
            except KeyError as exc:
                raise RuntimeError(
                    f"IBKR 成交方向无法识别：{raw_side or 'N/A'}"
                ) from exc
            time_value = getattr(execution, 'time', None)
            raw_order_id = getattr(execution, "orderId", None)
            broker_order_id = (
                str(raw_order_id).strip()
                if raw_order_id is not None
                else ""
            )
            exec_id = str(getattr(execution, "execId", "") or "").strip()
            if not broker_order_id or not exec_id:
                raise RuntimeError(
                    "IBKR 成交缺少 orderId 或 execId，无法安全去重和归属。"
                )
            rows.append(
                FillSnapshot(
                    broker_order_id=broker_order_id,
                    symbol=self._data_symbol(contract),
                    qty=float(getattr(execution, 'shares', 0.0) or 0.0),
                    price=float(getattr(execution, 'price', 0.0) or 0.0),
                    side=side,
                    filled_at=ensure_utc(time_value if isinstance(time_value, datetime) else None),
                    account=fill_account,
                    exec_id=exec_id,
                    perm_id=int(getattr(execution, "permId", 0) or 0) or None,
                    client_id=int(getattr(execution, "clientId", 0) or 0),
                    con_id=int(getattr(contract, "conId", 0) or 0) or None,
                    order_ref=(
                        str(getattr(execution, "orderRef", "") or "").strip()
                        or None
                    ),
                )
            )
        return rows

    def sync_state(self) -> BrokerStateSnapshot:
        """一次性同步券商全量状态。"""

        snapshot_asof = utc_now()
        snapshot_batch_id = f"ibkr-pos-{uuid4().hex}"
        target_account = self._target_account()
        return BrokerStateSnapshot(
            positions=self.positions(
                snapshot_asof=snapshot_asof,
                snapshot_batch_id=snapshot_batch_id,
            ),
            open_orders=self.open_orders(),
            completed_orders=self.completed_orders(),
            fills=self.recent_fills(),
            account_values=self.account_values(),
            account=target_account,
            state_complete=True,
            asof=snapshot_asof,
        )

    def cancel_order(
        self,
        broker_order_id: str,
        *,
        expected_order_ref: str | None = None,
        expected_perm_id: int | None = None,
        expected_client_id: int | None = None,
        expected_con_id: int | None = None,
    ) -> bool:
        """按账户、clientId、orderId 和可选持久化身份精确撤单。"""

        self.connect()
        target_account = self._target_account()
        target = str(broker_order_id)
        target_client_id = int(self.settings.ibkr_client_id)
        if (
            expected_client_id is not None
            and int(expected_client_id) != target_client_id
        ):
            raise RuntimeError(
                "IBKR_CANCEL_IDENTITY_MISMATCH: 本地 clientId 与当前连接不一致。"
            )
        matches: list[Any] = []
        for trade in self.ib.reqAllOpenOrders():
            order = getattr(trade, 'order', None)
            contract = getattr(trade, "contract", None)
            order_account = str(getattr(order, "account", "") or "").strip()
            if order_account != target_account:
                continue
            oid = str(getattr(order, 'orderId', ''))
            if oid != target:
                continue
            client_id = int(getattr(order, "clientId", -1))
            if client_id != target_client_id:
                continue
            order_ref = str(getattr(order, "orderRef", "") or "").strip()
            if expected_order_ref is not None and order_ref != expected_order_ref:
                raise RuntimeError(
                    "IBKR_CANCEL_IDENTITY_MISMATCH: orderRef 与持久化记录不一致。"
                )
            if not order_ref.startswith("NSQ-"):
                raise RuntimeError(
                    "IBKR_CANCEL_IDENTITY_MISMATCH: 目标订单缺少 Northstar "
                    "orderRef，已禁止撤单。"
                )
            observed_perm_id = int(getattr(order, "permId", 0) or 0) or None
            if (
                expected_perm_id is not None
                and observed_perm_id != int(expected_perm_id)
            ):
                raise RuntimeError(
                    "IBKR_CANCEL_IDENTITY_MISMATCH: permId 与持久化记录不一致。"
                )
            observed_con_id = (
                int(getattr(contract, "conId", 0) or 0) or None
            )
            if (
                expected_con_id is not None
                and observed_con_id != int(expected_con_id)
            ):
                raise RuntimeError(
                    "IBKR_CANCEL_IDENTITY_MISMATCH: conId 与持久化记录不一致。"
                )
            matches.append(order)
        if len(matches) > 1:
            raise RuntimeError(
                "IBKR_CANCEL_IDENTITY_AMBIGUOUS: account/clientId/orderId "
                "对应多条订单，已禁止撤单。"
            )
        if not matches:
            return False
        self.ib.cancelOrder(matches[0])
        with suppress(Exception):
            self.ib.sleep(0.5)
        return True

    def order_status(
        self,
        broker_order_id: str,
        *,
        expected_order_ref: str | None = None,
        expected_perm_id: int | None = None,
        expected_client_id: int | None = None,
        expected_con_id: int | None = None,
    ) -> dict[str, Any] | None:
        """读取单笔 IBKR 订单状态，包括已经离开 open book 的订单。"""

        self.connect()
        target_account = self._target_account()
        target = str(broker_order_id)
        cached_rows = self._order_rows(
            self.ib.trades(),
            target_account=target_account,
            state_name="本地订单缓存",
        )
        target_client_id = int(self.settings.ibkr_client_id)
        if (
            expected_client_id is not None
            and int(expected_client_id) != target_client_id
        ):
            raise RuntimeError(
                "IBKR_ORDER_IDENTITY_MISMATCH: 本地 clientId 与当前连接不一致。"
            )

        def validate_identity(row: dict[str, Any]) -> dict[str, Any]:
            observed_order_id = str(row.get("broker_order_id") or "").strip()
            observed_client_id = row.get("client_id")
            matches = [
                ("broker_order_id", observed_order_id, target),
                ("clientId", observed_client_id, target_client_id),
            ]
            for field_name, observed, expected in matches:
                if observed not in {"", None} and observed != expected:
                    raise RuntimeError(
                        "IBKR_ORDER_IDENTITY_MISMATCH: "
                        f"{field_name} 与持久化记录不一致。"
                    )
            order_ref = str(row.get("order_ref") or "").strip()
            if (
                expected_order_ref is not None
                and order_ref != expected_order_ref
            ):
                raise RuntimeError(
                    "IBKR_ORDER_IDENTITY_MISMATCH: orderRef 与持久化记录"
                    "不一致。"
                )
            if not order_ref.startswith("NSQ-"):
                raise RuntimeError(
                    "IBKR_ORDER_IDENTITY_MISMATCH: 目标订单缺少 Northstar "
                    "orderRef，已停止状态恢复。"
                )
            if (
                expected_perm_id is not None
                and row.get("perm_id") != int(expected_perm_id)
            ):
                raise RuntimeError(
                    "IBKR_ORDER_IDENTITY_MISMATCH: permId 与持久化记录不一致。"
                )
            if (
                expected_con_id is not None
                and row.get("con_id") != int(expected_con_id)
            ):
                raise RuntimeError(
                    "IBKR_ORDER_IDENTITY_MISMATCH: conId 与持久化记录不一致。"
                )
            return row

        def find_matching_row(
            rows: list[dict],
            *,
            allow_stable_identity: bool = False,
        ) -> dict[str, Any] | None:
            exact_matches = [
                row
                for row in rows
                if str(row.get("broker_order_id") or "").strip() == target
                and row.get("client_id") == target_client_id
            ]
            if len(exact_matches) > 1:
                raise RuntimeError(
                    "IBKR_ORDER_IDENTITY_AMBIGUOUS: account/clientId/orderId "
                    "对应多条订单，已停止状态恢复。"
                )
            if exact_matches:
                return validate_identity(exact_matches[0])
            if not allow_stable_identity:
                return None

            expected_ref = str(expected_order_ref or "").strip()
            if not expected_ref or expected_con_id is None:
                return None
            stable_candidates = [
                row
                for row in rows
                if str(row.get("order_ref") or "").strip() == expected_ref
            ]
            if len(stable_candidates) > 1:
                raise RuntimeError(
                    "IBKR_ORDER_IDENTITY_AMBIGUOUS: orderRef/permId "
                    "对应多条已完成订单，已停止状态恢复。"
                )
            if stable_candidates:
                row = stable_candidates[0]
                if (
                    row.get("broker_order_id") is None
                    or row.get("client_id") is None
                ):
                    return validate_identity(row)
                # 强身份碰撞到了另一笔仍有完整 client/order ID 的订单，
                # 不允许借稳定身份覆盖显式身份冲突。
                validate_identity(row)
            return None

        cached_match = find_matching_row(cached_rows)
        if cached_match is not None:
            return cached_match
        return find_matching_row(
            self.completed_orders(),
            allow_stable_identity=True,
        )
