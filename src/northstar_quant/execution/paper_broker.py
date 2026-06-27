"""纸面券商适配器。"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.config.settings import get_settings
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

_FINAL_ORDER_STATUSES = {
    "filled",
    "cancelled",
    "rejected",
    "inactive",
    "apicancelled",
}


class PaperBrokerAdapter(BrokerAdapter):
    """本地持久化纸面交易账户。

    这个适配器不只是“接单 mock”，而是维护一个最小可用的仿真账户状态：
    - cash / positions / avg_cost
    - open order 生命周期
    - partial fill / full fill
    - cancel
    - sync_state 返回完整账户快照
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.account = self.settings.ibkr_account or "paper-account"
        self.state_path = self.settings.storage_dir / "paper_broker_state.json"
        self.settings.storage_dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    def _empty_state(self) -> dict:
        return {
            "cash": float(self.settings.default_cash),
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

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            state = self._empty_state()
            self._state = state
            self._save_state()
            return state

        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        state = self._empty_state()
        state["cash"] = float(raw.get("cash", state["cash"]) or state["cash"])
        state["positions"] = dict(raw.get("positions", {}) or {})
        state["orders"] = dict(raw.get("orders", {}) or {})
        state["fills"] = list(raw.get("fills", []) or [])
        state["last_prices"] = {
            str(symbol).strip().upper(): float(price)
            for symbol, price in (raw.get("last_prices", {}) or {}).items()
            if str(symbol).strip()
        }
        return state

    def _save_state(self) -> None:
        tmp_path = self.state_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(self._state, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.state_path)

    def _reload_state(self) -> None:
        self._state = self._load_state()

    @staticmethod
    def _signed_qty(side: str, qty: float) -> float:
        return abs(qty) if str(side).upper() == "BUY" else -abs(qty)

    @staticmethod
    def _is_final_status(status: str | None) -> bool:
        return str(status or "").strip().lower() in _FINAL_ORDER_STATUSES

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
                )
            )
        return rows

    def _account_values(self) -> dict[str, float]:
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

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self._reload_state()
        submitted_at = utc_now()
        broker_order_id = f"paper-{uuid4().hex[:12]}"
        symbol = str(order.symbol).strip().upper()
        order_qty = abs(float(order.qty))
        reference_price = (
            float(order.reference_price)
            if order.reference_price is not None
            else None
        )
        self._set_last_price(symbol, reference_price)

        self._state["orders"][broker_order_id] = {
            "broker_order_id": broker_order_id,
            "strategy_id": order.strategy_id,
            "symbol": symbol,
            "side": str(order.side).upper(),
            "qty": order_qty,
            "filled_qty": 0.0,
            "remaining_qty": order_qty,
            "avg_fill_price": None,
            "status": "Submitted",
            "submitted_at": self._dt_to_str(submitted_at),
            "updated_at": self._dt_to_str(submitted_at),
            "order_type": str(order.order_type or "MKT").upper(),
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
            asof=asof,
        )

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
                    source="paper_state",
                )
            )
        return quotes

    def cancel_order(self, broker_order_id: str) -> bool:
        self._reload_state()
        order = self._state["orders"].get(str(broker_order_id))
        if order is None or self._is_final_status(order.get("status")):
            return False
        order["status"] = "Cancelled"
        order["updated_at"] = self._dt_to_str(utc_now())
        self._save_state()
        return True

    def get_name(self) -> str:
        return "paper"
