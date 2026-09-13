"""Identified external cash movements; balance deltas are not transfer evidence."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .amounts import decimal_text


@dataclass(frozen=True, slots=True)
class CashFlowFact:
    cash_flow_id: str
    amount: Decimal
    currency: str
    transferred_at: datetime
    available_at: datetime
    source_reference: str
    reverses_id: str | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not 1 <= len(value) <= 256
            for value in (self.cash_flow_id, self.source_reference)
        ):
            raise ValueError("cash flow requires bounded identity and source evidence")
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isupper()
            or not self.currency.isalpha()
        ):
            raise ValueError("cash flow requires an explicit currency")
        if self.reverses_id is not None and (
            not isinstance(self.reverses_id, str)
            or not 1 <= len(self.reverses_id) <= 256
            or self.reverses_id == self.cash_flow_id
        ):
            raise ValueError("cash flow reversal requires a distinct original identity")
        if (
            any(
                not isinstance(at, datetime) or at.utcoffset() != timedelta(0)
                for at in (self.transferred_at, self.available_at)
            )
            or self.available_at < self.transferred_at
        ):
            raise ValueError("cash flow requires causal UTC transfer and availability times")
        if (
            not isinstance(self.amount, Decimal)
            or not self.amount.is_finite()
            or not self.amount
            or len(self.amount.as_tuple().digits) > 34
            or self.amount.adjusted() > 33
        ):
            raise ValueError("cash flow requires a bounded exact nonzero signed amount")
        exponent = self.amount.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -18:
            raise ValueError("cash flow requires at most 18 decimal places")

    def to_dict(self) -> dict[str, object]:
        return {
            "cash_flow_id": self.cash_flow_id,
            "amount": decimal_text(self.amount),
            "currency": self.currency,
            "transferred_at": self.transferred_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "source_reference": self.source_reference,
            "reverses_id": self.reverses_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "CashFlowFact":
        names = {
            "cash_flow_id",
            "amount",
            "currency",
            "transferred_at",
            "available_at",
            "source_reference",
            "reverses_id",
        }
        if (
            set(value) != names
            or any(not isinstance(value[key], str) for key in names - {"reverses_id"})
            or (value["reverses_id"] is not None and not isinstance(value["reverses_id"], str))
        ):
            raise ValueError("persisted cash flow requires exact fields and decimal text")
        return cls(
            str(value["cash_flow_id"]),
            Decimal(str(value["amount"])),
            str(value["currency"]),
            datetime.fromisoformat(str(value["transferred_at"])),
            datetime.fromisoformat(str(value["available_at"])),
            str(value["source_reference"]),
            None if value["reverses_id"] is None else str(value["reverses_id"]),
        )
