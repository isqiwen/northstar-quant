"""Confirmed aggregate charges over explicitly identified unpriced fills.

An account-level commission observation does not establish this fact on its own.
The owner must verify the covered executions and source before applying it.
No allocation of the aggregate amount to individual fills is invented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from .amounts import decimal_text


@dataclass(frozen=True, slots=True)
class FeeFact:
    fee_id: str
    fill_ids: tuple[str, ...]
    amount: Decimal
    currency: str
    charged_at: datetime
    available_at: datetime
    source_reference: str
    supersedes_fee_id: str | None = None

    def __post_init__(self) -> None:
        if self.supersedes_fee_id is not None and (
            not isinstance(self.supersedes_fee_id, str)
            or not 1 <= len(self.supersedes_fee_id) <= 256
            or self.supersedes_fee_id == self.fee_id
        ):
            raise ValueError("fee revision requires a distinct prior charge identity")
        if any(
            not isinstance(value, str) or not 1 <= len(value) <= 256
            for value in (self.fee_id, self.source_reference)
        ):
            raise ValueError("fee requires bounded identity and verified source reference")
        if (
            not isinstance(self.fill_ids, tuple)
            or not 1 <= len(self.fill_ids) <= 1000
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 256 for value in self.fill_ids
            )
            or tuple(sorted(set(self.fill_ids))) != self.fill_ids
        ):
            raise ValueError("fee requires a bounded sorted unique set of execution identities")
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not all("A" <= c <= "Z" for c in self.currency)
        ):
            raise ValueError("fee requires an explicit currency")
        if (
            any(
                not isinstance(at, datetime) or at.utcoffset() != timedelta(0)
                for at in (self.charged_at, self.available_at)
            )
            or self.available_at < self.charged_at
        ):
            raise ValueError("fee requires causal UTC charge and availability times")
        if (
            not isinstance(self.amount, Decimal)
            or not self.amount.is_finite()
            or self.amount < 0
            or len(self.amount.as_tuple().digits) > 34
            or self.amount.adjusted() > 33
        ):
            raise ValueError("fee requires a bounded exact nonnegative charge")
        exponent = self.amount.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -18:
            raise ValueError("fee requires at most 18 decimal places")

    def delta_from(self, previous: FeeFact | None) -> Decimal:
        """A correction replaces an exact coverage amount; it never reallocates fills."""
        if self.supersedes_fee_id is None:
            if previous is not None:
                raise ValueError("initial charge cannot replace another fee")
            return self.amount
        if (
            previous is None
            or self.supersedes_fee_id != previous.fee_id
            or self.fill_ids != previous.fill_ids
            or self.currency != previous.currency
            or self.available_at < previous.available_at
            or self.charged_at < previous.charged_at
        ):
            raise ValueError("fee revision differs in coverage or precedes its prior charge")
        with localcontext() as context:
            context.prec = 192
            return self.amount - previous.amount

    def to_dict(self) -> dict[str, object]:
        return {
            "fee_id": self.fee_id,
            "fill_ids": list(self.fill_ids),
            "amount": decimal_text(self.amount),
            "currency": self.currency,
            "charged_at": self.charged_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "source_reference": self.source_reference,
            "supersedes_fee_id": self.supersedes_fee_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> FeeFact:
        try:
            fields = (
                "fee_id",
                "amount",
                "currency",
                "charged_at",
                "available_at",
                "source_reference",
            )
            if any(not isinstance(value[name], str) for name in fields):
                raise ValueError("persisted fee fields must be exact strings")
            prior = value["supersedes_fee_id"]
            if prior is not None and not isinstance(prior, str):
                raise ValueError("persisted fee revision must have a string identity")
            fills = value["fill_ids"]
            if not isinstance(fills, list) or any(not isinstance(item, str) for item in fills):
                raise ValueError("persisted fee executions must be strings")
            return cls(
                str(value["fee_id"]),
                tuple(fills),
                Decimal(str(value["amount"])),
                str(value["currency"]),
                datetime.fromisoformat(str(value["charged_at"])),
                datetime.fromisoformat(str(value["available_at"])),
                str(value["source_reference"]),
                prior,
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted fee fact") from error


@dataclass(frozen=True, slots=True)
class AppliedFee:
    fact: FeeFact
    cash: Decimal | None
    total_fees: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            **self.fact.to_dict(),
            "cash": None if self.cash is None else decimal_text(self.cash),
            "total_fees": decimal_text(self.total_fees),
        }
