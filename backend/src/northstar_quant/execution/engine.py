"""Bounded working-order projection and explicit replacement planning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .orders import OrderBudget, OrderStatus, OrderUpdate, PendingOrder


@dataclass(frozen=True, slots=True)
class Replacement:
    retained: PendingOrder | None
    cancel: PendingOrder | None
    submit: PendingOrder | None


@dataclass(frozen=True, slots=True)
class ExecutionEngine:
    """One working order for the current single-target execution path.

    Planning never cancels an order. Only a confirmed terminal update releases
    it. The owning transaction accepts identified account fills first and passes
    their newly applied quantity here; cumulative reports cannot invent fills.
    This reconstructible projection does not itself persist or send commands.
    """

    pending: PendingOrder | None = None
    observed_at: datetime | None = None

    def plan(
        self,
        desired: PendingOrder | None,
        *,
        retained_budget: OrderBudget | None = None,
    ) -> Replacement:
        prior = self.pending
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
        prior = self.pending
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
        return ExecutionEngine(None if terminal else update.order, update.at)
