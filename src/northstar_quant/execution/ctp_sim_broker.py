"""隔离的本地 CTP 语义仿真柜台。

该适配器不连接任何期货公司前置。它只用于演练具体合约、开平仓、异步回报、
持久化恢复和对账流程，不能证明真实 CTP SDK 或柜台已经可用。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from functools import wraps
import json
import math
from pathlib import Path
from typing import Any, TextIO, cast

from northstar_quant.common.enums import CtpOffset
from northstar_quant.common.order_identity import build_order_ref
from northstar_quant.common.order_status import is_final_order_status
from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.config.ctp_contract_mapping import (
    CtpContractMapping,
    load_ctp_contract_registry,
)
from northstar_quant.config.settings import get_settings, normalize_local_state_account
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    MarketQuoteSnapshot,
    OrderRequest,
    OrderResult,
    PositionSnapshot,
)
from northstar_quant.execution.pricing import normalize_symbols


fcntl: Any = None
msvcrt: Any = None
try:  # pragma: no cover - the active platform determines the covered branch.
    import fcntl as _fcntl
except ModuleNotFoundError:  # Windows does not provide fcntl.
    import msvcrt as _msvcrt

    msvcrt = _msvcrt
else:
    fcntl = _fcntl


def _locked_state(method):
    """让一次仿真柜台读改写在跨进程文件锁内完成。"""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._state_lock():
            self._state = self._load_state()
            return method(self, *args, **kwargs)

    return wrapped


def _lock_state_file(lock_file: TextIO) -> None:
    """获取跨进程状态文件锁，兼容 Linux 与 Windows。"""

    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        return

    assert msvcrt is not None
    lock_file.seek(0, 2)
    if lock_file.tell() == 0:
        # Windows 的 msvcrt.locking 不能锁定空文件中的字节范围。
        lock_file.write("0")
        lock_file.flush()
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)


def _unlock_state_file(lock_file: TextIO) -> None:
    """释放由 _lock_state_file 获取的文件锁。"""

    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return

    assert msvcrt is not None
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


class CtpSimBrokerAdapter(BrokerAdapter):
    """使用本地状态文件实现的净持仓期货仿真柜台。"""

    client_id = 1

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        mapping_path: str | Path | None = None,
        account: str | None = None,
    ) -> None:
        settings = get_settings()
        self.account = normalize_local_state_account(
            str(settings.ctp_sim_account if account is None else account)
        )
        if state_path is None:
            resolved_state_path = (
                settings.ctp_sim_state_path
                if account is None
                else settings.storage_dir
                / "brokers"
                / "ctp_sim"
                / self.account
                / "state.json"
            )
        else:
            resolved_state_path = Path(state_path)
        self.state_path = Path(resolved_state_path).resolve()
        self.registry = load_ctp_contract_registry(
            mapping_path or settings.ctp_sim_contract_mapping_path,
            expected_broker="ctp_sim",
        )
        self.default_cash = float(settings.default_cash)
        self._connected = False
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")
        with self._state_lock():
            self._state = self._load_state()

    @contextmanager
    def _state_lock(self):
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            _lock_state_file(lock_file)
            try:
                yield
            finally:
                _unlock_state_file(lock_file)

    def _empty_state(self) -> dict:
        return {
            "version": 1,
            "account": self.account,
            "balance": self.default_cash,
            "trading_day": utc_now().date().isoformat(),
            "positions": {},
            "orders": {},
            "fills": [],
            "quotes": {},
            "next_order_seq": 1,
            "next_exec_seq": 1,
        }

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            state = self._empty_state()
            self._state = state
            self._save_state()
            return state
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if int(payload.get("version", 0)) != 1:
            raise ValueError("CTP_SIM_STATE_VERSION_UNSUPPORTED: 仿真柜台状态版本不受支持。")
        if str(payload.get("account") or "").strip() != self.account:
            raise ValueError("CTP_SIM_ACCOUNT_MISMATCH: 状态文件账户与当前账户不一致。")
        return payload

    def _save_state(self) -> None:
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self._state, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.state_path)

    def _require_connected(self) -> None:
        if not self._connected:
            raise ConnectionError("CTP_SIM_DISCONNECTED: 仿真柜台尚未连接。")

    def connect(self) -> None:
        self._state = self._load_state()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def get_name(self) -> str:
        return "ctp_sim"

    def get_account(self) -> str:
        return self.account

    def get_client_id(self) -> int:
        return self.client_id

    def _mapping_for_order(self, order: OrderRequest) -> CtpContractMapping:
        mapping = self.registry.resolve_data_symbol(order.symbol)
        if (
            order.instrument_id is not None
            and str(order.instrument_id).strip().lower() != mapping.instrument_id
        ):
            raise ValueError("CTP_SIM_INSTRUMENT_MISMATCH: instrument_id 与映射不一致。")
        if (
            order.exchange_id is not None
            and str(order.exchange_id).strip().upper() != mapping.exchange_id
        ):
            raise ValueError("CTP_SIM_EXCHANGE_MISMATCH: exchange_id 与映射不一致。")
        if (
            order.volume_multiple is not None
            and int(order.volume_multiple) != mapping.volume_multiple
        ):
            raise ValueError("CTP_SIM_MULTIPLIER_MISMATCH: 合约乘数与映射不一致。")
        return mapping

    def prepare_order(self, order: OrderRequest) -> OrderRequest:
        mapping = self._mapping_for_order(order)
        order_ref = order.order_ref
        if order_ref is None and order.plan_id:
            order_ref = build_order_ref(order.plan_id, order.attempt_no)
        return replace(
            order,
            symbol=mapping.data_symbol,
            account=order.account or self.account,
            instrument_id=mapping.instrument_id,
            exchange_id=mapping.exchange_id,
            volume_multiple=mapping.volume_multiple,
            order_ref=order_ref,
        )

    @staticmethod
    def _positive(value: float | None, *, field_name: str) -> float:
        if value is None or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"CTP_SIM_ORDER_INVALID: {field_name} 必须是正有限数。")
        return float(value)

    def _validate_order(
        self,
        order: OrderRequest,
        mapping: CtpContractMapping,
    ) -> tuple[CtpOffset, float]:
        if order.account != self.account:
            raise ValueError("CTP_SIM_ACCOUNT_MISMATCH: 订单账户与仿真账户不一致。")
        if str(order.side).strip().upper() not in {"BUY", "SELL"}:
            raise ValueError("CTP_SIM_ORDER_INVALID: side 必须为 BUY 或 SELL。")
        qty = self._positive(order.qty, field_name="qty")
        if not math.isclose(qty, round(qty), abs_tol=1e-8):
            raise ValueError("CTP_SIM_ORDER_INVALID: 期货数量必须是整数手。")
        try:
            offset = cast(CtpOffset, CtpOffset.parse(str(order.ctp_offset or "")))
        except ValueError as exc:
            raise ValueError("CTP_SIM_OFFSET_REQUIRED: 订单必须提供有效开平仓标志。") from exc
        if mapping.exchange_id in {"SHFE", "INE"} and offset == CtpOffset.CLOSE:
            raise ValueError(
                "CTP_SIM_EXPLICIT_CLOSE_REQUIRED: SHFE/INE 必须明确平今或平昨。"
            )
        if mapping.exchange_id not in {"SHFE", "INE"} and offset in {
            CtpOffset.CLOSE_TODAY,
            CtpOffset.CLOSE_YESTERDAY,
        }:
            raise ValueError("CTP_SIM_CLOSE_OFFSET_INVALID: 当前交易所应使用 CLOSE。")
        order_type = str(order.order_type or "").strip().upper()
        if order_type not in {"MKT", "LMT"}:
            raise ValueError("CTP_SIM_ORDER_INVALID: order_type 必须为 MKT 或 LMT。")
        if order_type == "LMT":
            self._positive(order.limit_price, field_name="limit_price")
        price = self._positive(
            order.reference_price or order.limit_price,
            field_name="reference_price",
        )
        if offset == CtpOffset.OPEN:
            margin_rate = self._positive(order.margin_rate, field_name="margin_rate")
            if margin_rate > 1:
                raise ValueError("CTP_SIM_ORDER_INVALID: margin_rate 不能大于 1。")
            required_margin = qty * price * mapping.volume_multiple * margin_rate
            if required_margin > self._account_metrics()["available"] + 1e-8:
                raise ValueError("CTP_SIM_MARGIN_INSUFFICIENT: 开仓保证金超过可用资金。")
        return offset, price

    @_locked_state
    def submit_order(self, order: OrderRequest) -> OrderResult:
        self._require_connected()
        self._state = self._load_state()
        order = self.prepare_order(order)
        mapping = self._mapping_for_order(order)
        offset, price = self._validate_order(order, mapping)
        order_ref = str(order.order_ref or "").strip()
        if not order_ref:
            raise ValueError("CTP_SIM_ORDER_REF_REQUIRED: 仿真提交必须提供稳定 order_ref。")

        for existing in self._state["orders"].values():
            if str(existing.get("order_ref") or "") != order_ref:
                continue
            expected = (
                existing["symbol"],
                existing["side"],
                float(existing["qty"]),
                existing["ctp_offset"],
            )
            actual = (
                order.symbol,
                str(order.side).upper(),
                float(order.qty),
                offset.value,
            )
            if expected != actual:
                raise RuntimeError("CTP_SIM_IDEMPOTENCY_CONFLICT: order_ref 对应不同订单。")
            return OrderResult(
                accepted=True,
                broker_order_id=str(existing["broker_order_id"]),
                status=str(existing["status"]),
                message="仿真柜台幂等命中，未重复报单。",
                submitted_at=ensure_utc(datetime.fromisoformat(existing["submitted_at"])),
                replayed=True,
                client_id=self.client_id,
                perm_id=int(existing["perm_id"]),
            )

        sequence = int(self._state["next_order_seq"])
        self._state["next_order_seq"] = sequence + 1
        broker_order_id = f"CTPSIM-{sequence:08d}"
        submitted_at = utc_now()
        self._state["orders"][broker_order_id] = {
            "broker_order_id": broker_order_id,
            "order_ref": order_ref,
            "perm_id": sequence,
            "client_id": self.client_id,
            "account": self.account,
            "strategy_id": order.strategy_id,
            "symbol": mapping.data_symbol,
            "instrument_id": mapping.instrument_id,
            "exchange_id": mapping.exchange_id,
            "side": str(order.side).upper(),
            "qty": float(order.qty),
            "filled_qty": 0.0,
            "remaining_qty": float(order.qty),
            "status": "Submitted",
            "order_type": str(order.order_type).upper(),
            "limit_price": order.limit_price,
            "reference_price": price,
            "ctp_offset": offset.value,
            "volume_multiple": mapping.volume_multiple,
            "margin_rate": order.margin_rate,
            "fill_stage": 0,
            "submitted_at": submitted_at.isoformat(),
            "updated_at": submitted_at.isoformat(),
        }
        self._save_state()
        return OrderResult(
            accepted=True,
            broker_order_id=broker_order_id,
            status="Submitted",
            message=f"CTP 仿真柜台已受理：{broker_order_id}",
            submitted_at=submitted_at,
            client_id=self.client_id,
            perm_id=sequence,
        )

    @_locked_state
    def seed_market_quotes(
        self,
        prices: dict[str, float],
        *,
        asof: datetime | None = None,
    ) -> None:
        """向仿真柜台注入本轮本地行情；来源会明确标记为 ctp_sim。"""

        self._require_connected()
        self._state = self._load_state()
        quote_time = ensure_utc(asof or utc_now()).isoformat()
        for symbol in normalize_symbols(prices):
            try:
                mapping = self.registry.resolve_data_symbol(symbol)
            except ValueError:
                continue
            last = self._positive(prices[symbol], field_name=f"{symbol}.price")
            self._state["quotes"][symbol] = {
                "last": last,
                "bid": max(last - mapping.price_tick, mapping.price_tick),
                "ask": last + mapping.price_tick,
                "asof": quote_time,
            }
        self._save_state()

    @_locked_state
    def roll_trading_day(self, trading_day: date) -> None:
        """显式推进交易日，把今仓结转为昨仓。"""

        self._require_connected()
        self._state = self._load_state()
        current = date.fromisoformat(str(self._state["trading_day"]))
        if trading_day <= current:
            raise ValueError("CTP_SIM_TRADING_DAY_INVALID: 新交易日必须晚于当前交易日。")
        for position in self._state["positions"].values():
            position["long_yesterday_qty"] += position["long_today_qty"]
            position["long_today_qty"] = 0.0
            position["short_yesterday_qty"] += position["short_today_qty"]
            position["short_today_qty"] = 0.0
        self._state["trading_day"] = trading_day.isoformat()
        self._save_state()

    def _quote_price(self, order: dict) -> float | None:
        quote = self._state["quotes"].get(order["symbol"])
        if quote is not None:
            return float(quote["last"])
        reference = order.get("reference_price")
        return float(reference) if reference is not None else None

    def _is_fillable(self, order: dict, price: float | None) -> bool:
        if price is None:
            return False
        if order["order_type"] == "MKT":
            return True
        limit_price = float(order["limit_price"])
        if order["side"] == "BUY":
            return limit_price >= price
        return limit_price <= price

    @staticmethod
    def _next_fill_qty(order: dict) -> float:
        remaining = float(order["remaining_qty"])
        if order["order_type"] == "LMT" and int(order["fill_stage"]) == 0 and remaining > 1:
            return max(1.0, math.floor(remaining / 2))
        return remaining

    def _empty_position(self, order: dict) -> dict:
        return {
            "instrument_id": order["instrument_id"],
            "exchange_id": order["exchange_id"],
            "volume_multiple": int(order["volume_multiple"]),
            "margin_rate": float(order.get("margin_rate") or 0.0),
            "long_today_qty": 0.0,
            "long_yesterday_qty": 0.0,
            "short_today_qty": 0.0,
            "short_yesterday_qty": 0.0,
            "long_avg_cost": 0.0,
            "short_avg_cost": 0.0,
        }

    @staticmethod
    def _close_buckets(position: dict, prefix: str, offset: CtpOffset, qty: float) -> None:
        today_key = f"{prefix}_today_qty"
        yesterday_key = f"{prefix}_yesterday_qty"
        if offset == CtpOffset.CLOSE_TODAY:
            keys: tuple[str, ...] = (today_key,)
        elif offset == CtpOffset.CLOSE_YESTERDAY:
            keys = (yesterday_key,)
        else:
            keys = (yesterday_key, today_key)
        remaining = qty
        for key in keys:
            reduced = min(float(position[key]), remaining)
            position[key] = float(position[key]) - reduced
            remaining -= reduced
        if remaining > 1e-8:
            raise ValueError("CTP_SIM_CLOSE_POSITION_EXCEEDED: 平仓数量超过可用今昨仓。")

    def _apply_fill(self, order: dict, qty: float, price: float) -> None:
        symbol = order["symbol"]
        position = self._state["positions"].setdefault(
            symbol,
            self._empty_position(order),
        )
        offset = cast(CtpOffset, CtpOffset.parse(order["ctp_offset"]))
        side = order["side"]
        multiplier = int(order["volume_multiple"])
        if offset == CtpOffset.OPEN:
            prefix = "long" if side == "BUY" else "short"
            total_before = float(position[f"{prefix}_today_qty"]) + float(
                position[f"{prefix}_yesterday_qty"]
            )
            avg_key = f"{prefix}_avg_cost"
            position[avg_key] = (
                float(position[avg_key]) * total_before + price * qty
            ) / (total_before + qty)
            position[f"{prefix}_today_qty"] += qty
            position["margin_rate"] = float(order["margin_rate"])
        else:
            prefix = "long" if side == "SELL" else "short"
            avg_cost = float(position[f"{prefix}_avg_cost"])
            self._close_buckets(position, prefix, offset, qty)
            pnl_per_unit = price - avg_cost if prefix == "long" else avg_cost - price
            self._state["balance"] = float(self._state["balance"]) + (
                pnl_per_unit * qty * multiplier
            )
            remaining = float(position[f"{prefix}_today_qty"]) + float(
                position[f"{prefix}_yesterday_qty"]
            )
            if remaining <= 1e-8:
                position[f"{prefix}_avg_cost"] = 0.0

        if all(
            float(position[key]) <= 1e-8
            for key in (
                "long_today_qty",
                "long_yesterday_qty",
                "short_today_qty",
                "short_yesterday_qty",
            )
        ):
            self._state["positions"].pop(symbol, None)

    def _process_orders(self) -> None:
        now = utc_now()
        for order in self._state["orders"].values():
            if is_final_order_status(order["status"]):
                continue
            price = self._quote_price(order)
            if not self._is_fillable(order, price):
                continue
            assert price is not None
            fill_qty = self._next_fill_qty(order)
            self._apply_fill(order, fill_qty, price)
            sequence = int(self._state["next_exec_seq"])
            self._state["next_exec_seq"] = sequence + 1
            self._state["fills"].append(
                {
                    "exec_id": f"CTPSIM-EXEC-{sequence:08d}",
                    "broker_order_id": order["broker_order_id"],
                    "order_ref": order["order_ref"],
                    "perm_id": order["perm_id"],
                    "client_id": self.client_id,
                    "account": self.account,
                    "symbol": order["symbol"],
                    "instrument_id": order["instrument_id"],
                    "exchange_id": order["exchange_id"],
                    "side": order["side"],
                    "qty": fill_qty,
                    "price": price,
                    "ctp_offset": order["ctp_offset"],
                    "filled_at": now.isoformat(),
                }
            )
            order["filled_qty"] = float(order["filled_qty"]) + fill_qty
            order["remaining_qty"] = max(float(order["qty"]) - order["filled_qty"], 0.0)
            order["fill_stage"] = int(order["fill_stage"]) + 1
            order["status"] = (
                "Filled" if order["remaining_qty"] <= 1e-8 else "PartiallyFilled"
            )
            order["updated_at"] = now.isoformat()

    def _mark_price(self, symbol: str, position: dict) -> float:
        quote = self._state["quotes"].get(symbol)
        if quote is not None:
            return float(quote["last"])
        long_qty = float(position["long_today_qty"]) + float(
            position["long_yesterday_qty"]
        )
        return float(
            position["long_avg_cost"] if long_qty > 0 else position["short_avg_cost"]
        )

    def _account_metrics(self) -> dict[str, float]:
        unrealized = 0.0
        margin = 0.0
        gross_notional = 0.0
        for symbol, position in self._state["positions"].items():
            price = self._mark_price(symbol, position)
            multiplier = int(position["volume_multiple"])
            long_qty = float(position["long_today_qty"]) + float(
                position["long_yesterday_qty"]
            )
            short_qty = float(position["short_today_qty"]) + float(
                position["short_yesterday_qty"]
            )
            unrealized += (
                (price - float(position["long_avg_cost"])) * long_qty * multiplier
                + (float(position["short_avg_cost"]) - price) * short_qty * multiplier
            )
            notional = (long_qty + short_qty) * price * multiplier
            gross_notional += notional
            margin += notional * float(position["margin_rate"])
        frozen_margin = sum(
            float(order["remaining_qty"])
            * float(order["reference_price"])
            * int(order["volume_multiple"])
            * float(order["margin_rate"])
            for order in self._state["orders"].values()
            if (
                not is_final_order_status(order["status"])
                and order["ctp_offset"] == CtpOffset.OPEN.value
            )
        )
        equity = float(self._state["balance"]) + unrealized
        return {
            "equity": equity,
            "available": equity - margin - frozen_margin,
            "margin": margin,
            "frozen_margin": frozen_margin,
            "gross_notional": gross_notional,
            "unrealized": unrealized,
        }

    @staticmethod
    def _order_snapshot(order: dict) -> dict:
        return dict(order)

    @_locked_state
    def sync_state(self) -> BrokerStateSnapshot:
        self._require_connected()
        self._state = self._load_state()
        self._process_orders()
        self._save_state()
        asof = utc_now()
        positions: list[PositionSnapshot] = []
        for symbol, position in sorted(self._state["positions"].items()):
            long_qty = float(position["long_today_qty"]) + float(
                position["long_yesterday_qty"]
            )
            short_qty = float(position["short_today_qty"]) + float(
                position["short_yesterday_qty"]
            )
            qty = long_qty - short_qty
            price = self._mark_price(symbol, position)
            avg_cost = (
                float(position["long_avg_cost"])
                if qty > 0
                else float(position["short_avg_cost"])
            )
            positions.append(
                PositionSnapshot(
                    symbol=symbol,
                    qty=qty,
                    avg_cost=avg_cost,
                    market_price=price,
                    market_value=qty * price * int(position["volume_multiple"]),
                    sellable_qty=abs(qty),
                    account=self.account,
                    instrument_id=position["instrument_id"],
                    exchange_id=position["exchange_id"],
                    long_today_qty=float(position["long_today_qty"]),
                    long_yesterday_qty=float(position["long_yesterday_qty"]),
                    short_today_qty=float(position["short_today_qty"]),
                    short_yesterday_qty=float(position["short_yesterday_qty"]),
                    asof=asof,
                )
            )
        open_orders = [
            self._order_snapshot(order)
            for order in self._state["orders"].values()
            if not is_final_order_status(order["status"])
        ]
        completed_orders = [
            self._order_snapshot(order)
            for order in self._state["orders"].values()
            if is_final_order_status(order["status"])
        ]
        fills = [
            FillSnapshot(
                broker_order_id=row["broker_order_id"],
                symbol=row["symbol"],
                qty=float(row["qty"]),
                price=float(row["price"]),
                side=row["side"],
                filled_at=ensure_utc(datetime.fromisoformat(row["filled_at"])),
                account=self.account,
                exec_id=row["exec_id"],
                order_ref=row["order_ref"],
                perm_id=int(row["perm_id"]),
                client_id=self.client_id,
                instrument_id=row["instrument_id"],
                exchange_id=row["exchange_id"],
                ctp_offset=row["ctp_offset"],
            )
            for row in self._state["fills"]
        ]
        metrics = self._account_metrics()
        return BrokerStateSnapshot(
            positions=positions,
            open_orders=open_orders,
            completed_orders=completed_orders,
            fills=fills,
            account_values={
                "Account": self.account,
                "Balance": metrics["equity"],
                "DynamicEquity": metrics["equity"],
                "NetLiquidation": metrics["equity"],
                "Available": metrics["available"],
                "AvailableFunds": metrics["available"],
                "CurrMargin": metrics["margin"],
                "FrozenMargin": metrics["frozen_margin"],
                "GrossPositionValue": metrics["gross_notional"],
                "UnrealizedPnL": metrics["unrealized"],
            },
            account=self.account,
            state_complete=True,
            asof=asof,
        )

    @_locked_state
    def get_market_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
        self._require_connected()
        self._state = self._load_state()
        quotes: list[MarketQuoteSnapshot] = []
        for symbol in normalize_symbols(symbols):
            row = self._state["quotes"].get(symbol)
            if row is None:
                continue
            quotes.append(
                MarketQuoteSnapshot(
                    symbol=symbol,
                    bid=float(row["bid"]),
                    ask=float(row["ask"]),
                    last=float(row["last"]),
                    market_price=float(row["last"]),
                    asof=ensure_utc(datetime.fromisoformat(row["asof"])),
                    source="ctp_sim_market_data",
                )
            )
        return quotes

    @_locked_state
    def cancel_order(self, broker_order_id: str) -> bool:
        self._require_connected()
        self._state = self._load_state()
        order = self._state["orders"].get(str(broker_order_id))
        if order is None or is_final_order_status(order["status"]):
            return False
        order["status"] = "Cancelled"
        order["updated_at"] = utc_now().isoformat()
        self._save_state()
        return True

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
        self._require_connected()
        self._state = self._load_state()
        order = self._state["orders"].get(str(broker_order_id))
        if order is None:
            return False
        expected = {
            "order_ref": order_ref,
            "perm_id": perm_id,
            "client_id": client_id,
            "instrument_id": instrument_id,
            "exchange_id": exchange_id,
        }
        for key, value in expected.items():
            if value is not None and str(order.get(key)) != str(value):
                raise ValueError(f"CTP_SIM_CANCEL_IDENTITY_MISMATCH: {key} 不一致。")
        return self.cancel_order(broker_order_id)

    @_locked_state
    def get_order_status(self, broker_order_id: str) -> dict | None:
        self._require_connected()
        self._state = self._load_state()
        order = self._state["orders"].get(str(broker_order_id))
        return dict(order) if order is not None else None

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
        row = self.get_order_status(broker_order_id)
        if row is None:
            return None
        expected = {
            "order_ref": order_ref,
            "perm_id": perm_id,
            "client_id": client_id,
            "instrument_id": instrument_id,
            "exchange_id": exchange_id,
        }
        for key, value in expected.items():
            if value is not None and str(row.get(key)) != str(value):
                return None
        return row
