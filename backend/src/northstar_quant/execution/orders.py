"""Bounded order requests; requests are not confirmed execution facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
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

    @property
    def remaining_lots(self) -> int:
        return self.quantity_lots - self.filled_lots

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
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> PendingOrder:
        try:
            quantity = value["quantity_lots"]
            filled = value["filled_lots"]
            if type(quantity) is not int or type(filled) is not int:
                raise ValueError("order quantity must be an integer")
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
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted pending order") from error


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
