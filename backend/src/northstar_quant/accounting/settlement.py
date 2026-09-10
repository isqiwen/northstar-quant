"""Identified daily variation settlement; never a simulated closing trade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from .amounts import decimal_text


@dataclass(frozen=True, slots=True)
class SettlementFact:
    settlement_id: str
    contract_id: UUID
    trading_day: date
    next_trading_day: date
    settled_at: datetime
    available_at: datetime
    price: Decimal
    source_reference: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not 1 <= len(value) <= 256
            for value in (self.settlement_id, self.source_reference)
        ):
            raise ValueError("settlement requires bounded identity and source evidence")
        if (
            not isinstance(self.contract_id, UUID)
            or type(self.trading_day) is not date
            or type(self.next_trading_day) is not date
            or self.next_trading_day <= self.trading_day
        ):
            raise ValueError("settlement requires a contract and explicit next trading day")
        if (
            any(
                not isinstance(at, datetime) or at.utcoffset() != timedelta(0)
                for at in (self.settled_at, self.available_at)
            )
            or self.available_at < self.settled_at
        ):
            raise ValueError("settlement requires causal UTC event and availability times")
        if not isinstance(self.price, Decimal) or not self.price.is_finite() or self.price <= 0:
            raise ValueError("settlement requires an exact positive price")
        exponent = self.price.as_tuple().exponent
        if (
            not isinstance(exponent, int)
            or exponent < -18
            or self.price.adjusted() > 33
            or len(self.price.as_tuple().digits) > 34
        ):
            raise ValueError("settlement price exceeds the bounded financial domain")

    def to_dict(self) -> dict[str, object]:
        return {
            "settlement_id": self.settlement_id,
            "contract_id": str(self.contract_id),
            "trading_day": self.trading_day.isoformat(),
            "next_trading_day": self.next_trading_day.isoformat(),
            "settled_at": self.settled_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "price": decimal_text(self.price),
            "source_reference": self.source_reference,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SettlementFact:
        try:
            names = (
                "settlement_id",
                "contract_id",
                "trading_day",
                "next_trading_day",
                "settled_at",
                "available_at",
                "price",
                "source_reference",
            )
            if any(not isinstance(value[name], str) for name in names):
                raise ValueError("persisted settlement fields must be exact strings")
            return cls(
                str(value["settlement_id"]),
                UUID(str(value["contract_id"])),
                date.fromisoformat(str(value["trading_day"])),
                date.fromisoformat(str(value["next_trading_day"])),
                datetime.fromisoformat(str(value["settled_at"])),
                datetime.fromisoformat(str(value["available_at"])),
                Decimal(str(value["price"])),
                str(value["source_reference"]),
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted settlement fact") from error


@dataclass(frozen=True, slots=True)
class AppliedSettlement:
    fact: SettlementFact
    variation_pnl: Decimal
    cash: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            **self.fact.to_dict(),
            "variation_pnl": decimal_text(self.variation_pnl),
            "cash": decimal_text(self.cash),
        }
