"""Identified execution facts and their applied ledger records, independent of storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.positions import Position
from northstar_quant.execution.orders import Offset, Side


@dataclass(frozen=True, slots=True)
class FillFact:
    """One uniquely identified fill, not an order's cumulative filled quantity."""

    fill_id: str
    order_id: str
    contract_id: UUID
    observation_id: UUID | None
    filled_at: datetime
    trading_day: date
    side: Side
    offset: Offset
    quantity_lots: int
    price: Decimal
    fee: Decimal
    available_at: datetime

    def __post_init__(self) -> None:
        for identity in (self.fill_id, self.order_id):
            if not isinstance(identity, str) or not 1 <= len(identity) <= 256:
                raise ValueError("fill requires bounded nonempty fill and order identities")
        if (
            not isinstance(self.contract_id, UUID)
            or self.observation_id is not None
            and not isinstance(self.observation_id, UUID)
            or not isinstance(self.side, Side)
            or not isinstance(self.offset, Offset)
        ):
            raise ValueError("fill requires a canonical contract and side")
        if (
            not isinstance(self.filled_at, datetime)
            or self.filled_at.utcoffset() != timedelta(0)
            or not isinstance(self.available_at, datetime)
            or self.available_at.utcoffset() != timedelta(0)
            or self.available_at < self.filled_at
            or type(self.trading_day) is not date
        ):
            raise ValueError(
                "fill requires causal UTC execution/availability times and explicit trading day"
            )
        if type(self.quantity_lots) is not int or not 1 <= self.quantity_lots <= 1_000_000_000:
            raise ValueError("fill quantity must be a positive integer number of lots")
        for name in ("price", "fee"):
            value = getattr(self, name)
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or (value <= 0 if name == "price" else value < 0)
                or len(value.as_tuple().digits) > 34
                or value.adjusted() > 33
            ):
                raise ValueError("fill requires bounded positive price and nonnegative fee")
            exponent = value.as_tuple().exponent
            if not isinstance(exponent, int) or exponent < -18:
                raise ValueError("fill money must use at most 18 decimal places")

    def to_dict(self) -> dict[str, object]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "contract_id": str(self.contract_id),
            "observation_id": None if self.observation_id is None else str(self.observation_id),
            "filled_at": self.filled_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "trading_day": self.trading_day.isoformat(),
            "side": self.side.value,
            "offset": self.offset.value,
            "quantity_lots": self.quantity_lots,
            "price": decimal_text(self.price),
            "fee": decimal_text(self.fee),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> FillFact:
        """Read fact fields, including from an AppliedFill's flat ledger record."""

        try:
            names = (
                "fill_id",
                "order_id",
                "contract_id",
                "filled_at",
                "available_at",
                "trading_day",
                "side",
            )
            if any(not isinstance(value[name], str) for name in names):
                raise ValueError("persisted fill identities and times must be strings")
            price, fee = value["price"], value["fee"]
            if not isinstance(price, str) or not isinstance(fee, str):
                raise ValueError("persisted fill money must be exact decimal strings")
            quantity = value["quantity_lots"]
            if type(quantity) is not int:
                raise ValueError("persisted fill quantity must be an integer")
            observation = value["observation_id"]
            if observation is not None and not isinstance(observation, str):
                raise ValueError("persisted observation identity must be a UUID string or null")
            return cls(
                str(value["fill_id"]),
                str(value["order_id"]),
                UUID(str(value["contract_id"])),
                None if observation is None else UUID(observation),
                datetime.fromisoformat(str(value["filled_at"])),
                date.fromisoformat(str(value["trading_day"])),
                Side(str(value["side"])),
                Offset(str(value["offset"])),
                quantity,
                Decimal(price),
                Decimal(fee),
                datetime.fromisoformat(str(value["available_at"])),
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted fill fact") from error


@dataclass(frozen=True, slots=True)
class AppliedFill:
    fact: FillFact
    realized_pnl: Decimal
    position_lots: int
    cash: Decimal
    total_fees: Decimal
    gross_position: Position

    def to_dict(self) -> dict[str, object]:
        return {
            **self.fact.to_dict(),
            "realized_pnl": decimal_text(self.realized_pnl),
            "position_lots": self.position_lots,
            "cash": decimal_text(self.cash),
            "total_fees": decimal_text(self.total_fees),
            "gross_position": self.gross_position.to_dict(),
        }
