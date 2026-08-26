"""纸面券商适配器。"""

from __future__ import annotations
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
import math
from threading import RLock
from uuid import uuid4

from sqlalchemy.orm import Session

from northstar_quant.foundation.common.time import ensure_utc, utc_now
from northstar_quant.foundation.common.order_status import is_final_order_status
from northstar_quant.foundation.config.settings import get_settings, normalize_simulator_account
from northstar_quant.trading_execution.broker.broker_base import BrokerAdapter
from northstar_quant.trading_execution.broker.contracts import BrokerCapabilities, BrokerConnectionState, BrokerIdentity, BrokerMode, BrokerStatus
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    MarketQuoteSnapshot,
    OrderRequest,
    OrderResult,
    PositionSnapshot,
)
from northstar_quant.trading_execution.execution.pricing import normalize_symbols
from northstar_quant.trading_execution.broker.simulated_state import (
    LockedSimulatedBrokerState,
    PostgresSimulatedBrokerStateRepository,
    SessionFactory,
    SimulatedBrokerStateEvidence,
)


def _locked_state(method):
    """Run one Paper broker operation inside its PostgreSQL state transaction."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._state_transaction(action=method.__name__):
            return method(self, *args, **kwargs)

    return wrapped


class PaperBrokerAdapter(BrokerAdapter):
    """PostgreSQL 持久化纸面交易账户。

    这个适配器不只是“接单 mock”，而是维护一个最小可用的仿真账户状态：
    - cash / positions / avg_cost
    - open order 生命周期
    - partial fill / full fill
    - cancel
    - sync_state 返回完整账户快照
    """

    def __init__(
        self,
        *,
        account: str | None = None,
        default_cash: float | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self.settings = get_settings()
        self.account = normalize_simulator_account(
            str(self.settings.paper_account if account is None else account)
        )
        resolved_default_cash = float(
            self.settings.default_cash if default_cash is None else default_cash
        )
        if not math.isfinite(resolved_default_cash) or resolved_default_cash <= 0:
            raise ValueError("PAPER_DEFAULT_CASH_INVALID")
        self.default_cash = resolved_default_cash
        # One adapter instance can be shared by a durable submission path and
        # lifecycle polling. Keep its caller-owned SQLAlchemy Session scoped to
        # the invoking operation; a second thread must wait rather than reuse
        # an uncommitted submission Session or observe its phantom snapshot.
        self._state_lock = RLock()
        self._state_session: Session | None = None
        self._active_state: LockedSimulatedBrokerState | None = None
        self._active_state_action: str | None = None
        self._state_repository = PostgresSimulatedBrokerStateRepository(
            broker="paper",
            account=self.account,
            schema_version=1,
            state_factory=self._empty_state,
            state_validator=self._validate_state,
            session_factory=session_factory,
        )
        self._state = self._load_state()

    @contextmanager
    def submission_transaction(self, session: Session):
        """Bind durable paper submission to the caller's PostgreSQL transaction."""

        if not isinstance(session, Session):
            raise TypeError("PAPER_POSTGRESQL_SESSION_REQUIRED")
        with self._state_lock:
            if self._state_session is not None and self._state_session is not session:
                raise RuntimeError("PAPER_STATE_SESSION_ALREADY_BOUND")
            previous_session = self._state_session
            self._state_session = session
            try:
                yield
            finally:
                self._state_session = previous_session

    @contextmanager
    def _state_transaction(self, *, action: str):
        with self._state_lock:
            if self._active_state is not None:
                raise RuntimeError("PAPER_STATE_TRANSACTION_REENTRANT")
            with self._state_repository.locked_state(session=self._state_session) as locked:
                self._active_state = locked
                self._active_state_action = action
                self._state = locked.state
                try:
                    yield
                finally:
                    self._active_state = None
                    self._active_state_action = None

    def _empty_state(self) -> dict:
        return {
            "version": 1,
            "broker": "paper",
            "account": self.account,
            "cash": self.default_cash,
            "positions": {},
            "orders": {},
            "fills": [],
            "last_prices": {},
        }

    @staticmethod
    def _dt_to_str(value) -> str | None:
        if value is None:
            return None
        return ensure_utc(value).isoformat()

    @staticmethod
    def _dt_from_str(value: str | None):
        if not value:
            return None
        return ensure_utc(datetime.fromisoformat(value))

    def _validate_state(self, payload: dict) -> None:
        required = {
            "version",
            "broker",
            "account",
            "cash",
            "positions",
            "orders",
            "fills",
            "last_prices",
        }
        if not required.issubset(payload):
            raise ValueError("PAPER_POSTGRESQL_STATE_FIELDS_MISSING")
        if int(payload.get("version", 0)) != 1:
            raise ValueError("PAPER_POSTGRESQL_STATE_VERSION_UNSUPPORTED")
        if payload.get("broker") != "paper":
            raise ValueError("PAPER_POSTGRESQL_STATE_BROKER_MISMATCH")
        if str(payload.get("account") or "").strip() != self.account:
            raise ValueError("PAPER_POSTGRESQL_STATE_ACCOUNT_MISMATCH")
        if not math.isfinite(float(payload["cash"])):
            raise ValueError("PAPER_POSTGRESQL_STATE_CASH_INVALID")
        if (
            not isinstance(payload["positions"], dict)
            or not isinstance(payload["orders"], dict)
            or not isinstance(payload["fills"], list)
            or not isinstance(payload["last_prices"], dict)
        ):
            raise ValueError("PAPER_POSTGRESQL_STATE_SHAPE_INVALID")

    def _load_state(self) -> dict:
        with self._state_lock:
            if self._active_state is not None:
                return self._active_state.state
            if self._state_session is not None:
                return self._state_repository.read_state_in_session(self._state_session)
            return self._state_repository.read_state()

    def simulator_state_evidence(self) -> SimulatedBrokerStateEvidence:
        """Return verified PostgreSQL state metadata without exposing payloads."""

        with self._state_lock:
            if self._active_state is not None:
                raise RuntimeError("PAPER_STATE_EVIDENCE_REQUIRES_IDLE_ADAPTER")
            return self._state_repository.current_evidence(session=self._state_session)

    def _save_state(self) -> None:
        if self._active_state is None or self._active_state_action is None:
            raise RuntimeError("PAPER_POSTGRESQL_STATE_TRANSACTION_REQUIRED")
        self._active_state.persist(action=self._active_state_action)

    def _reload_state(self) -> None:
        with self._state_lock:
            self._state = self._load_state()

    @staticmethod
    def _signed_qty(side: str, qty: float) -> float:
        return abs(qty) if str(side).upper() == "BUY" else -abs(qty)

    @staticmethod
    def _is_final_status(status: str | None) -> bool:
        return is_final_order_status(status)

    def _set_last_price(self, symbol: str, price: float | None) -> None:
        if price is None or price <= 0:
            return
        self._state["last_prices"][str(symbol).strip().upper()] = float(price)

    def _reference_price_for_order(self, order: dict) -> float | None:
        reference_price = order.get("reference_price")
        if reference_price is not None:
            return float(reference_price)

        symbol = str(order.get("symbol") or "").strip().upper()
        if symbol in self._state["last_prices"]:
            return float(self._state["last_prices"][symbol])

        limit_price = order.get("limit_price")
        return float(limit_price) if limit_price is not None else None

    def _mark_price_for_symbol(self, symbol: str, position: dict) -> float | None:
        normalized_symbol = str(symbol).strip().upper()
        if normalized_symbol in self._state["last_prices"]:
            return float(self._state["last_prices"][normalized_symbol])
        market_price = position.get("market_price")
        if market_price is not None:
            return float(market_price)
        avg_cost = position.get("avg_cost")
        if avg_cost is not None:
            return float(avg_cost)
        return None

    def _is_fillable(self, order: dict, reference_price: float | None) -> bool:
        order_type = str(order.get("order_type") or "MKT").upper()
        if order_type == "MKT":
            return reference_price is not None

        limit_price = order.get("limit_price")
        if limit_price is None or reference_price is None:
            return False

        if str(order.get("side") or "").upper() == "BUY":
            return float(limit_price) >= float(reference_price)
        return float(limit_price) <= float(reference_price)

    def _fill_price(self, order: dict, reference_price: float | None) -> float | None:
        mode = str(self.settings.paper_fill_price_mode or "close").strip().lower()
        limit_price = order.get("limit_price")

        if mode == "limit" and limit_price is not None:
            return float(limit_price)
        if mode in {"reference", "close"} and reference_price is not None:
            return float(reference_price)

        if limit_price is not None and reference_price is not None:
            if str(order.get("side") or "").upper() == "BUY":
                return min(float(limit_price), float(reference_price))
            return max(float(limit_price), float(reference_price))
        if reference_price is not None:
            return float(reference_price)
        if limit_price is not None:
            return float(limit_price)
        return None

    @staticmethod
    def _next_fill_qty(order: dict) -> float:
        remaining_qty = float(order.get("remaining_qty", 0.0) or 0.0)
        if remaining_qty <= 1e-8:
            return 0.0

        order_type = str(order.get("order_type") or "MKT").upper()
        fill_stage = int(order.get("fill_stage", 0) or 0)
        total_qty = float(order.get("qty", remaining_qty) or remaining_qty)

        if order_type == "MKT":
            return remaining_qty

        if fill_stage == 0 and total_qty > 1.0:
            return min(remaining_qty, round(total_qty * 0.5, 6))
        return remaining_qty

    def _apply_fill(self, order: dict, fill_qty: float, fill_price: float, filled_at) -> None:
        symbol = str(order.get("symbol") or "").strip().upper()
        signed_fill_qty = self._signed_qty(str(order.get("side") or ""), float(fill_qty))
        current_position = self._state["positions"].get(
            symbol,
            {"qty": 0.0, "avg_cost": None, "market_price": None},
        )
        current_qty = float(current_position.get("qty", 0.0) or 0.0)
        current_avg_cost = current_position.get("avg_cost")
        current_avg_cost = float(current_avg_cost) if current_avg_cost is not None else None
        new_qty = current_qty + signed_fill_qty
        new_avg_cost: float | None

        self._state["cash"] -= signed_fill_qty * float(fill_price)
        self._set_last_price(symbol, fill_price)

        if abs(current_qty) < 1e-8:
            new_avg_cost = float(fill_price)
        elif current_qty * signed_fill_qty > 0:
            total_abs_qty = abs(current_qty) + abs(signed_fill_qty)
            existing_cost = abs(current_qty) * float(current_avg_cost or fill_price)
            fill_cost = abs(signed_fill_qty) * float(fill_price)
            new_avg_cost = (existing_cost + fill_cost) / total_abs_qty
        elif abs(signed_fill_qty) < abs(current_qty):
            new_avg_cost = current_avg_cost
        elif abs(signed_fill_qty) == abs(current_qty):
            new_avg_cost = None
        else:
            new_avg_cost = float(fill_price)

        if abs(new_qty) <= 1e-8:
            self._state["positions"].pop(symbol, None)
        else:
            self._state["positions"][symbol] = {
                "qty": float(new_qty),
                "avg_cost": float(new_avg_cost) if new_avg_cost is not None else None,
                "market_price": float(fill_price),
                "updated_at": self._dt_to_str(filled_at),
            }

        previous_filled_qty = float(order.get("filled_qty", 0.0) or 0.0)
        new_filled_qty = previous_filled_qty + float(fill_qty)
        remaining_qty = max(float(order.get("qty", 0.0) or 0.0) - new_filled_qty, 0.0)
        order["filled_qty"] = float(new_filled_qty)
        order["remaining_qty"] = float(remaining_qty)
        order["avg_fill_price"] = float(fill_price)
        order["fill_stage"] = int(order.get("fill_stage", 0) or 0) + 1
        order["updated_at"] = self._dt_to_str(filled_at)
        order["status"] = "Filled" if remaining_qty <= 1e-8 else "PartiallyFilled"

        self._state["fills"].append(
            {
                "broker_order_id": str(order.get("broker_order_id") or ""),
                "symbol": symbol,
                "qty": float(fill_qty),
                "price": float(fill_price),
                "side": str(order.get("side") or "").upper(),
                "filled_at": self._dt_to_str(filled_at),
                "account": self.account,
                "order_ref": order.get("order_ref"),
                "exec_id": f"paper-exec-{uuid4().hex}",
            }
        )
        self._state["fills"] = self._state["fills"][-500:]

    def _advance_orders(self, asof) -> None:
        for order in self._state["orders"].values():
            if self._is_final_status(order.get("status")):
                continue

            reference_price = self._reference_price_for_order(order)
            self._set_last_price(str(order.get("symbol") or ""), reference_price)
            if not self._is_fillable(order, reference_price):
                order["status"] = "Submitted"
                order["updated_at"] = self._dt_to_str(asof)
                continue

            fill_price = self._fill_price(order, reference_price)
            fill_qty = self._next_fill_qty(order)
            if fill_price is None or fill_qty <= 1e-8:
                continue
            self._apply_fill(order, fill_qty, fill_price, asof)

    def _active_orders(self) -> list[dict]:
        rows: list[dict] = []
        for row in self._state["orders"].values():
            remaining_qty = float(row.get("remaining_qty", 0.0) or 0.0)
            if self._is_final_status(row.get("status")) or remaining_qty <= 1e-8:
                continue
            rows.append(
                {
                    "broker_order_id": row["broker_order_id"],
                    "account": self.account,
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "qty": float(row["qty"]),
                    "filled_qty": float(row.get("filled_qty", 0.0) or 0.0),
                    "remaining_qty": remaining_qty,
                    "avg_fill_price": row.get("avg_fill_price"),
                    "status": row.get("status") or "Submitted",
                    "submitted_at": self._dt_from_str(row.get("submitted_at")),
                    "order_type": row.get("order_type"),
                    "limit_price": row.get("limit_price"),
                }
            )
        return sorted(rows, key=lambda item: str(item["broker_order_id"]))

    def _position_snapshots(self, asof) -> list[PositionSnapshot]:
        rows: list[PositionSnapshot] = []
        for symbol, position in sorted(self._state["positions"].items()):
            qty = float(position.get("qty", 0.0) or 0.0)
            if abs(qty) <= 1e-8:
                continue
            market_price = self._mark_price_for_symbol(symbol, position)
            rows.append(
                PositionSnapshot(
                    symbol=symbol,
                    qty=qty,
                    avg_cost=(
                        float(position["avg_cost"])
                        if position.get("avg_cost") is not None
                        else None
                    ),
                    market_price=market_price,
                    market_value=(qty * market_price) if market_price is not None else None,
                    sellable_qty=max(qty, 0.0),
                    account=self.account,
                    asof=asof,
                )
            )
        return rows

    def _fill_snapshots(self) -> list[FillSnapshot]:
        rows: list[FillSnapshot] = []
        for fill in self._state["fills"]:
            rows.append(
                FillSnapshot(
                    broker_order_id=str(fill.get("broker_order_id") or ""),
                    symbol=str(fill.get("symbol") or ""),
                    qty=float(fill.get("qty", 0.0) or 0.0),
                    price=float(fill.get("price", 0.0) or 0.0),
                    side=str(fill.get("side") or "").upper(),
                    filled_at=self._dt_from_str(fill.get("filled_at")),
                    account=str(fill.get("account") or self.account),
                    exec_id=str(fill.get("exec_id") or "") or None,
                    order_ref=str(fill.get("order_ref") or "") or None,
                )
            )
        return rows

    def _account_values(self) -> dict[str, float | str]:
        market_value_total = 0.0
        gross_position_value = 0.0
        for symbol, position in self._state["positions"].items():
            qty = float(position.get("qty", 0.0) or 0.0)
            market_price = self._mark_price_for_symbol(symbol, position)
            if market_price is None:
                continue
            market_value = qty * market_price
            market_value_total += market_value
            gross_position_value += abs(market_value)

        cash = float(self._state["cash"])
        net_liquidation = cash + market_value_total
        return {
            "NetLiquidation": float(net_liquidation),
            "EquityWithLoanValue": float(net_liquidation),
            "AvailableFunds": float(cash),
            "BuyingPower": float(cash),
            "GrossPositionValue": float(gross_position_value),
            "CashBalance": float(cash),
            "TotalCashValue": float(cash),
        }

    @_locked_state
    def submit_order(self, order: OrderRequest) -> OrderResult:
        self._reload_state()
        side = str(order.side).strip().upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("订单方向必须为 BUY 或 SELL")
        order_type = str(order.order_type or "").strip().upper()
        if order_type not in {"MKT", "LMT"}:
            raise ValueError("订单类型必须为 MKT 或 LMT")
        if order_type == "LMT" and order.limit_price is None:
            raise ValueError("限价单必须提供 limit_price")

        submitted_at = utc_now()
        symbol = str(order.symbol).strip().upper()
        order_qty = abs(float(order.qty))
        order_ref = str(order.order_ref or "").strip() or f"PAPER-{uuid4().hex[:20]}"
        for existing in self._state["orders"].values():
            if str(existing.get("order_ref") or "") != order_ref:
                continue
            expected = (
                str(existing.get("symbol") or ""),
                str(existing.get("side") or ""),
                float(existing.get("qty", 0.0) or 0.0),
                str(existing.get("order_type") or ""),
                existing.get("limit_price"),
            )
            actual = (
                symbol,
                side,
                order_qty,
                order_type,
                float(order.limit_price) if order.limit_price is not None else None,
            )
            if expected != actual:
                raise RuntimeError("PAPER_IDEMPOTENCY_CONFLICT: order_ref 对应不同订单。")
            return OrderResult(
                accepted=True,
                broker_order_id=str(existing["broker_order_id"]),
                status=str(existing["status"]),
                message="纸面柜台幂等命中，未重复报单。",
                submitted_at=self._dt_from_str(existing.get("submitted_at")),
                replayed=True,
            )

        broker_order_id = f"paper-{uuid4().hex[:12]}"
        reference_price = (
            float(order.reference_price)
            if order.reference_price is not None
            else None
        )
        self._set_last_price(symbol, reference_price)

        self._state["orders"][broker_order_id] = {
            "broker_order_id": broker_order_id,
            "order_ref": order_ref,
            "strategy_id": order.strategy_id,
            "symbol": symbol,
            "side": side,
            "qty": order_qty,
            "filled_qty": 0.0,
            "remaining_qty": order_qty,
            "avg_fill_price": None,
            "status": "Submitted",
            "submitted_at": self._dt_to_str(submitted_at),
            "updated_at": self._dt_to_str(submitted_at),
            "order_type": order_type,
            "limit_price": (
                float(order.limit_price)
                if order.limit_price is not None
                else None
            ),
            "target_weight": order.target_weight,
            "order_semantic": order.order_semantic,
            "account": order.account or self.account,
            "reason": order.reason,
            "reference_price": reference_price,
            "fill_stage": 0,
        }
        self._save_state()
        return OrderResult(
            accepted=True,
            broker_order_id=broker_order_id,
            status="Submitted",
            message=f"纸面订单已接受：{symbol} {order.side} {order.qty}",
            submitted_at=submitted_at,
        )

    @_locked_state
    def sync_state(self) -> BrokerStateSnapshot:
        self._reload_state()
        asof = utc_now()
        self._advance_orders(asof)
        self._save_state()
        return BrokerStateSnapshot(
            positions=self._position_snapshots(asof),
            open_orders=self._active_orders(),
            fills=self._fill_snapshots(),
            account_values=self._account_values(),
            account=self.account,
            asof=asof,
        )

    @_locked_state
    def get_market_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
        self._reload_state()
        asof = utc_now()
        quotes: list[MarketQuoteSnapshot] = []
        for symbol in normalize_symbols(symbols):
            price = self._state["last_prices"].get(symbol)
            if price is None:
                continue
            quotes.append(
                MarketQuoteSnapshot(
                    symbol=symbol,
                    last=float(price),
                    close=float(price),
                    market_price=float(price),
                    asof=asof,
                    source="paper_postgresql_state",
                )
            )
        return quotes

    @_locked_state
    def cancel_order(self, broker_order_id: str) -> bool:
        self._reload_state()
        order = self._state["orders"].get(str(broker_order_id))
        if order is None or self._is_final_status(order.get("status")):
            return False
        order["status"] = "Cancelled"
        order["updated_at"] = self._dt_to_str(utc_now())
        self._save_state()
        return True

    @_locked_state
    def get_order_status(self, broker_order_id: str) -> dict | None:
        """读取纸面账户中的完整订单状态，包括已经完成的订单。"""

        self._reload_state()
        order = self._state["orders"].get(str(broker_order_id))
        if order is None:
            return None
        return {
            "broker_order_id": str(order.get("broker_order_id") or broker_order_id),
            "order_ref": order.get("order_ref"),
            "symbol": str(order.get("symbol") or ""),
            "side": str(order.get("side") or ""),
            "qty": float(order.get("qty", 0.0) or 0.0),
            "filled_qty": float(order.get("filled_qty", 0.0) or 0.0),
            "remaining_qty": float(order.get("remaining_qty", 0.0) or 0.0),
            "avg_fill_price": order.get("avg_fill_price"),
            "status": str(order.get("status") or ""),
        }

    def get_account(self) -> str:
        return self.account

    def broker_status(self) -> BrokerStatus:
        return BrokerStatus(
            BrokerIdentity(self.get_name(), BrokerMode.PAPER, self.account, None),
            BrokerConnectionState.CONNECTED,
            BrokerCapabilities(True, True, True, True, False),
            datetime.now(UTC),
        )

    def get_name(self) -> str:
        return "paper"
