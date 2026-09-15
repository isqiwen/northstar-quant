"""Audit a simulated order history against individually accepted account fills."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from northstar_quant.accounting.fills import FillFact
from northstar_quant.execution.orders import OrderStatus, OrderUpdate, PendingOrder

_TERMINAL = frozenset({OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED})


class OrderHistory:
    """Transient verification projection, never a sender or a reservation store.

    The net-target execution path permits one working order per contract. Each
    step's accepted fill precedes its order notifications. Broker reconciliation
    has different ordering and terminal-correction semantics and does not use
    this simulated-history audit.
    """

    def __init__(self) -> None:
        self._orders: dict[str, OrderUpdate] = {}
        self._filled: dict[str, int] = {}
        self._fill_ids: set[str] = set()
        self._working: dict[UUID, str] = {}
        self._unreported: set[str] = set()
        self._last_fill_at: dict[str, datetime] = {}

    def accept_fill(self, fact: FillFact) -> None:
        prior = self._orders.get(fact.order_id)
        if (
            fact.fill_id in self._fill_ids
            or prior is None
            or prior.status in _TERMINAL
            or fact.contract_id != prior.order.contract_id
            or fact.side is not prior.order.side
            or fact.offset is not prior.order.offset
            or not prior.order.submitted_at < fact.filled_at < prior.order.expires_at
            or fact.filled_at < prior.at
            or not prior.order.minimum_fill_price <= fact.price <= prior.order.maximum_fill_price
        ):
            raise ValueError("simulated fill does not match a working authorized order")
        quantity = self._filled[fact.order_id] + fact.quantity_lots
        if quantity > prior.order.quantity_lots:
            raise ValueError("simulated fills exceed the requested order quantity")
        self._filled[fact.order_id] = quantity
        self._fill_ids.add(fact.fill_id)
        self._unreported.add(fact.order_id)
        self._last_fill_at[fact.order_id] = fact.filled_at

    def observe(self, update: OrderUpdate, *, at: datetime) -> None:
        order = update.order
        prior = self._orders.get(order.order_id)
        if update.at > at:
            raise ValueError("order notification is later than its owning step")
        if prior is None:
            if (
                update.status is not OrderStatus.SUBMITTED
                or order.filled_lots
                or update.at != order.submitted_at
            ):
                raise ValueError("order history must start with an unfilled submission")
            if order.contract_id in self._working:
                raise ValueError("research execution permits only one working order per contract")
            self._filled[order.order_id] = 0
        elif (
            prior.status in _TERMINAL
            or update.at < prior.at
            or replace(order, filled_lots=prior.order.filled_lots) != prior.order
        ):
            raise ValueError("order history changes fixed terms or a terminal state")
        if (
            order.filled_lots != self._filled[order.order_id]
            or order.order_id in self._last_fill_at
            and update.at < self._last_fill_at[order.order_id]
        ):
            raise ValueError("order cumulative fills differ from individual account facts")
        self._orders[order.order_id] = update
        if update.status in _TERMINAL:
            self._working.pop(order.contract_id, None)
        else:
            self._working[order.contract_id] = order.order_id
        self._unreported.discard(order.order_id)

    def require_working(self, pending: tuple[PendingOrder, ...]) -> None:
        working = {
            identity: self._orders[order_id].order for identity, order_id in self._working.items()
        }
        expected = {order.contract_id: order for order in pending}
        if len(expected) != len(pending) or working != expected or self._unreported:
            raise ValueError("pending order differs from its complete execution history")
