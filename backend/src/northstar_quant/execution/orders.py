"""Bounded order requests; requests are not confirmed execution facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text


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

    def __post_init__(self) -> None:
        if (
            not isinstance(self.order_id, str)
            or not 1 <= len(self.order_id) <= 256
            or not isinstance(self.observation_id, UUID)
            or not isinstance(self.side, Side)
            or not isinstance(self.offset, Offset)
            or type(self.quantity_lots) is not int
            or not 1 <= self.quantity_lots <= 1_000_000_000
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

    def to_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "observation_id": str(self.observation_id),
            "submitted_at": self.submitted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "side": self.side.value,
            "offset": self.offset.value,
            "quantity_lots": self.quantity_lots,
            "minimum_fill_price": decimal_text(self.minimum_fill_price),
            "maximum_fill_price": decimal_text(self.maximum_fill_price),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> PendingOrder:
        try:
            quantity = value["quantity_lots"]
            if type(quantity) is not int:
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
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted pending order") from error


def intraday_offset(position_lots: int, side: Side, quantity_lots: int) -> Offset:
    """Plan one single-sided, same-day action after Risk has bounded its size.

    This research execution model has no yesterday inventory or frozen orders.
    A reversal needs a confirmed close followed by a separately authorized open.
    """
    if (
        type(position_lots) is not int
        or not isinstance(side, Side)
        or type(quantity_lots) is not int
        or quantity_lots <= 0
    ):
        raise ValueError("invalid intraday order action")
    closing = (position_lots > 0 and side is Side.SELL) or (position_lots < 0 and side is Side.BUY)
    if closing and quantity_lots > abs(position_lots):
        raise ValueError("close must not cross through zero into a new position")
    return Offset.CLOSE_TODAY if closing else Offset.OPEN
