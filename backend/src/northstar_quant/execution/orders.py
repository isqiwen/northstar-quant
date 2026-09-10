"""Bounded order requests; requests are not confirmed execution facts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.positions import Position


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Offset(StrEnum):
    OPEN = "OPEN"
    CLOSE_TODAY = "CLOSE_TODAY"
    CLOSE_YESTERDAY = "CLOSE_YESTERDAY"


@dataclass(frozen=True, slots=True)
class PendingOrder:
    order_id: str
    observation_id: UUID
    submitted_at: datetime
    expires_at: datetime
    side: Side
    offset: Offset
    quantity_lots: int
    minimum_fill_price: Decimal
    maximum_fill_price: Decimal
    filled_lots: int = 0
    fee_budget_per_lot: Decimal = field(kw_only=True)
    margin_budget_per_lot: Decimal = field(kw_only=True)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.order_id, str)
            or not 1 <= len(self.order_id) <= 256
            or not isinstance(self.observation_id, UUID)
            or not isinstance(self.side, Side)
            or not isinstance(self.offset, Offset)
            or type(self.quantity_lots) is not int
            or not 1 <= self.quantity_lots <= 1_000_000_000
            or type(self.filled_lots) is not int
            or not 0 <= self.filled_lots <= self.quantity_lots
        ):
            raise ValueError("order requires stable identity, side and positive integer lots")
        if (
            self.submitted_at.utcoffset() != timedelta(0)
            or self.expires_at.utcoffset() != timedelta(0)
            or self.expires_at <= self.submitted_at
        ):
            raise ValueError("order requires a positive UTC lifetime")
        if (
            not self.minimum_fill_price.is_finite()
            or not self.maximum_fill_price.is_finite()
            or not Decimal(0) < self.minimum_fill_price <= self.maximum_fill_price
        ):
            raise ValueError("order requires an exact positive fill-price interval")
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value < 0
            for value in (self.fee_budget_per_lot, self.margin_budget_per_lot)
        ) or (self.offset is not Offset.OPEN and self.margin_budget_per_lot != 0):
            raise ValueError(
                "order requires fixed nonnegative fee/margin budgets; closes hold no new margin"
            )

    @property
    def remaining_lots(self) -> int:
        return self.quantity_lots - self.filled_lots

    def record_fill(self, quantity_lots: int, *, at: datetime, reason: str) -> OrderUpdate:
        """Advance once for an individually accepted fill in the owner's transaction.

        The fact ledger owns fill deduplication. A cumulative broker quantity is
        not a fill, and this operation does not change account facts or release
        a reservation. Confirmed facts can arrive after an authorization expires.
        """
        if type(quantity_lots) is not int or not 0 < quantity_lots <= self.remaining_lots:
            raise ValueError("accepted fill exceeds the order's remaining quantity")
        updated = replace(self, filled_lots=self.filled_lots + quantity_lots)
        status = OrderStatus.FILLED if updated.remaining_lots == 0 else OrderStatus.PARTIALLY_FILLED
        return OrderUpdate(updated, status, at, reason)

    def observe(self, *, at: datetime, reason: str) -> OrderUpdate:
        if self.remaining_lots == 0:
            raise ValueError("a filled order is no longer working")
        status = OrderStatus.PARTIALLY_FILLED if self.filled_lots else OrderStatus.SUBMITTED
        return OrderUpdate(self, status, at, reason)

    def cancel(self, *, at: datetime, reason: str) -> OrderUpdate:
        """Record confirmed cancellation, never a request to cancel at a broker."""
        return OrderUpdate(self, OrderStatus.CANCELED, at, reason)

    def expire(self, *, at: datetime, reason: str) -> OrderUpdate:
        """Record an execution environment's established expiry, not a lost connection."""
        return OrderUpdate(self, OrderStatus.EXPIRED, at, reason)

    def fits_authorization(
        self,
        *,
        side: Side,
        offset: Offset,
        quantity_lots: int,
        minimum_fill_price: Decimal,
        maximum_fill_price: Decimal,
        expires_at: datetime,
    ) -> bool:
        """Retain the exact old order only when every remaining bound still fits."""
        return (
            self.remaining_lots > 0
            and self.side is side
            and self.offset is offset
            and self.remaining_lots <= quantity_lots
            and minimum_fill_price <= self.minimum_fill_price
            and self.maximum_fill_price <= maximum_fill_price
            and self.expires_at <= expires_at
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "observation_id": str(self.observation_id),
            "submitted_at": self.submitted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "side": self.side.value,
            "offset": self.offset.value,
            "quantity_lots": self.quantity_lots,
            "filled_lots": self.filled_lots,
            "minimum_fill_price": decimal_text(self.minimum_fill_price),
            "maximum_fill_price": decimal_text(self.maximum_fill_price),
            "fee_budget_per_lot": decimal_text(self.fee_budget_per_lot),
            "margin_budget_per_lot": decimal_text(self.margin_budget_per_lot),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> PendingOrder:
        try:
            quantity = value["quantity_lots"]
            filled = value["filled_lots"]
            if type(quantity) is not int or type(filled) is not int:
                raise ValueError("order quantity must be an integer")
            for name in (
                "minimum_fill_price",
                "maximum_fill_price",
                "fee_budget_per_lot",
                "margin_budget_per_lot",
            ):
                if not isinstance(value[name], str):
                    raise ValueError(
                        "persisted order prices and budgets must be exact decimal strings"
                    )
            return cls(
                str(value["order_id"]),
                UUID(str(value["observation_id"])),
                datetime.fromisoformat(str(value["submitted_at"])),
                datetime.fromisoformat(str(value["expires_at"])),
                Side(str(value["side"])),
                Offset(str(value["offset"])),
                quantity,
                Decimal(str(value["minimum_fill_price"])),
                Decimal(str(value["maximum_fill_price"])),
                filled,
                fee_budget_per_lot=Decimal(str(value["fee_budget_per_lot"])),
                margin_budget_per_lot=Decimal(str(value["margin_budget_per_lot"])),
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted pending order") from error


def reservation(order: PendingOrder | None) -> dict[str, object]:
    """Hold the remaining budget until the owner records fills or a terminal fact.

    Wall time, an expired authorization or a cancellation request cannot release
    this view. The execution owner removes a confirmed terminal order. A broker
    UNKNOWN outcome would retain it; this function never consults an SDK/account.
    """
    with localcontext() as context:
        context.prec = 192
        context.rounding = ROUND_HALF_EVEN
        remaining = 0 if order is None else order.remaining_lots
        return {
            "reserved_fee": decimal_text(
                Decimal(0) if order is None else remaining * order.fee_budget_per_lot
            ),
            "reserved_margin": decimal_text(
                Decimal(0) if order is None else remaining * order.margin_budget_per_lot
            ),
            "reserved_close_lots": remaining
            if order is not None and order.offset is not Offset.OPEN
            else 0,
        }


def order_slice(position: Position, side: Side, quantity_lots: int) -> tuple[Offset, int]:
    """One explicit age-specific order; never cross zero or close the wrong lot age.

    Yesterday is closed first. A remaining target is reconsidered only after
    this order's confirmed fill; the caller cannot assume the entire target filled.
    """
    if (
        not isinstance(position, Position)
        or not isinstance(side, Side)
        or type(quantity_lots) is not int
        or quantity_lots <= 0
    ):
        raise ValueError("order requires a position, side and positive integer quantity")
    today = position.long_today if side is Side.SELL else position.short_today
    yesterday = position.long_yesterday if side is Side.SELL else position.short_yesterday
    if today + yesterday == 0:
        return Offset.OPEN, quantity_lots
    if quantity_lots > today + yesterday:
        raise ValueError("close must not cross through zero into a new position")
    if yesterday:
        return Offset.CLOSE_YESTERDAY, min(quantity_lots, yesterday)
    return Offset.CLOSE_TODAY, quantity_lots


class OrderStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    """Explicit simulated order outcome, retained with the owning event transaction."""

    order: PendingOrder
    status: OrderStatus
    at: datetime
    reason: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.order, PendingOrder)
            or not isinstance(self.status, OrderStatus)
            or not isinstance(self.at, datetime)
            or self.at.utcoffset() != timedelta(0)
            or self.at < self.order.submitted_at
            or not isinstance(self.reason, str)
            or not 1 <= len(self.reason) <= 256
        ):
            raise ValueError("order update requires its exact request, UTC time and reason")
        if (
            self.status is OrderStatus.FILLED
            and self.order.remaining_lots != 0
            or self.status is OrderStatus.PARTIALLY_FILLED
            and not 0 < self.order.filled_lots < self.order.quantity_lots
            or self.status is OrderStatus.SUBMITTED
            and self.order.filled_lots != 0
            or self.status in {OrderStatus.CANCELED, OrderStatus.EXPIRED}
            and self.order.remaining_lots == 0
            or self.status is OrderStatus.EXPIRED
            and self.at < self.order.expires_at
        ):
            raise ValueError("order status conflicts with remaining quantity or lifetime")

    def to_dict(self) -> dict[str, object]:
        return {
            **self.order.to_dict(),
            "status": self.status.value,
            "at": self.at.isoformat(),
            "reason": self.reason,
            "remaining_lots": self.order.remaining_lots,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> OrderUpdate:
        order = PendingOrder.from_dict(value)
        if value["remaining_lots"] != order.remaining_lots:
            raise ValueError("order update remaining quantity does not conserve requested lots")
        return cls(
            order,
            OrderStatus(str(value["status"])),
            datetime.fromisoformat(str(value["at"])),
            str(value["reason"]),
        )
