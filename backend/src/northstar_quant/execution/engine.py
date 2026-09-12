"""Bounded working-order projection and explicit replacement planning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from .orders import OrderBudget, OrderStatus, OrderUpdate, PendingOrder


@dataclass(frozen=True, slots=True)
class Replacement:
    retained: PendingOrder | None
    cancel: PendingOrder | None
    submit: PendingOrder | None


@dataclass(frozen=True, slots=True)
class ExecutionEngine:
    """One account working-order projection, with one net-target order per contract.

    Planning never cancels an order. Only a confirmed terminal update releases
    it. The owning transaction accepts identified account fills first and passes
    their newly applied quantity here; cumulative reports cannot invent fills.
    This reconstructible projection does not itself persist or send commands.
    """

    working: tuple[PendingOrder, ...] = ()
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.working, tuple)
            or any(
                not isinstance(order, PendingOrder) or not order.remaining_lots
                for order in self.working
            )
            or len({order.contract_id for order in self.working}) != len(self.working)
            or len({order.order_id for order in self.working}) != len(self.working)
        ):
            raise ValueError("working orders require unique contracts and order identities")
        if self.working and self.observed_at is None:
            raise ValueError("working orders require an observation time")
        if self.observed_at is not None and (
            self.observed_at.utcoffset() != timedelta(0)
            or any(order.submitted_at > self.observed_at for order in self.working)
        ):
            raise ValueError("working order observation must cover all submissions")
        object.__setattr__(
            self, "working", tuple(sorted(self.working, key=lambda order: order.contract_id.int))
        )

    def pending_for(self, contract_id: UUID) -> PendingOrder | None:
        return next((order for order in self.working if order.contract_id == contract_id), None)

    def plan(
        self,
        contract_id: UUID,
        desired: PendingOrder | None,
        *,
        retained_budget: OrderBudget | None = None,
    ) -> Replacement:
        prior = self.pending_for(contract_id)
        if not isinstance(contract_id, UUID) or (
            desired is not None and desired.contract_id != contract_id
        ):
            raise ValueError("replacement plan belongs to a different contract")
        if prior is not None and desired is not None and retained_budget is None:
            raise ValueError("retention requires current risk budgets at the original price bound")
        if (
            prior is not None
            and desired is not None
            and retained_budget is not None
            and prior.contract_id == desired.contract_id
            and prior.budget.covers(retained_budget)
            and prior.fits_authorization(
                side=desired.side,
                offset=desired.offset,
                quantity_lots=desired.quantity_lots,
                minimum_fill_price=desired.minimum_fill_price,
                maximum_fill_price=desired.maximum_fill_price,
                expires_at=desired.expires_at,
            )
        ):
            return Replacement(prior, None, None)
        return Replacement(None, prior, desired)

    def record(self, update: OrderUpdate, *, accepted_fill_lots: int = 0) -> ExecutionEngine:
        if type(accepted_fill_lots) is not int or accepted_fill_lots < 0:
            raise ValueError("execution requires newly accepted integer fill lots")
        prior = self.pending_for(update.order.contract_id)
        if any(
            order.order_id == update.order.order_id
            and order.contract_id != update.order.contract_id
            for order in self.working
        ):
            raise ValueError("working order identity belongs to another contract")
        if self.observed_at is not None and update.at < self.observed_at:
            raise ValueError("execution update time moved backwards")
        if prior is None:
            if (
                update.status is not OrderStatus.SUBMITTED
                or update.order.filled_lots
                or accepted_fill_lots
                or update.at != update.order.submitted_at
            ):
                raise ValueError("working order must begin with its confirmed submission")
        elif (
            replace(update.order, filled_lots=prior.filled_lots) != prior
            or update.order.filled_lots != prior.filled_lots + accepted_fill_lots
        ):
            raise ValueError("order update changes fixed terms or unconfirmed fill quantities")
        terminal = update.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
        others = tuple(
            order for order in self.working if order.contract_id != update.order.contract_id
        )
        return ExecutionEngine(others if terminal else others + (update.order,), update.at)
