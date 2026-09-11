"""Explicit source/session meaning pinned with each research publication.

A declared trading day is never inferred from a wall-clock date. These structural
checks do not verify an exchange holiday calendar or the supplier's declaration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from northstar_quant.market_data.sessions import SessionWindow


@dataclass(frozen=True, slots=True)
class ImportSpec:
    exchange: str
    symbol: str
    product: str
    timezone: str
    currency: str
    quantity_unit: str
    price_tick: Decimal
    multiplier: Decimal
    trading_day: date
    session_kind: str
    session_open: datetime
    session_close: datetime
    source_name: str
    source_reference: str
    availability_basis: str
    availability_note: str

    def __post_init__(self) -> None:
        for name in ("exchange", "symbol", "product", "quantity_unit"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,31}", value) is None
            ):
                raise ValueError(
                    f"data.{name} must be an uppercase identifier of at most 32 characters"
                )
        if (
            not isinstance(self.source_name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.source_name) is None
        ):
            raise ValueError(
                "data.source_name must be a source identifier of at most 64 characters"
            )
        for name in ("source_reference", "availability_note"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 1024
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"data.{name} must be nonempty text of at most 1024 characters")
        if not isinstance(self.availability_basis, str) or self.availability_basis not in {
            "SOURCE_DECLARED",
            "FINAL_REVISED",
            "SYNTHETIC",
            "LOCAL_CAPTURE_RECONSTRUCTED",
        }:
            raise ValueError(
                "data.availability_basis is not a supported current information-clock basis"
            )
        if not isinstance(self.currency, str) or re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise ValueError("data.currency must be a three-letter uppercase currency")
        try:
            timezone = ZoneInfo(self.timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("data.timezone must name an IANA timezone") from error
        for name in ("price_tick", "multiplier"):
            value = getattr(self, name)
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or not Decimal(0) < value < Decimal("1000000000000")
                or int(value.as_tuple().exponent) < -12
            ):
                raise ValueError(f"data.{name} must be positive with at most 12 decimal places")
        if type(self.trading_day) is not date:
            raise ValueError("data.trading_day must be a date")
        for name in ("session_open", "session_close"):
            at = getattr(self, name)
            if (
                not isinstance(at, datetime)
                or at.utcoffset() != timedelta(0)
                or at.second != 0
                or at.microsecond != 0
            ):
                raise ValueError(f"data.{name} must be UTC minute-aligned")
        if self.session_open >= self.session_close:
            raise ValueError("data.session_open must precede session_close")
        opened = self.session_open.astimezone(timezone).date()
        closed = self.session_close.astimezone(timezone).date()
        if self.session_kind == "DAY":
            if opened != self.trading_day or closed != self.trading_day:
                raise ValueError("DAY session must lie on its declared local trading day")
        elif self.session_kind == "NIGHT":
            # Friday night may belong to Monday. No calendar-day + 1 inference.
            if opened >= self.trading_day or closed > self.trading_day:
                raise ValueError("NIGHT session must begin before its declared trading day")
            if self.session_close - self.session_open >= timedelta(days=1):
                raise ValueError("NIGHT session must be one continuous window shorter than a day")
        else:
            raise ValueError("data.session_kind must be DAY or NIGHT")

    @property
    def window(self) -> SessionWindow:
        return SessionWindow(self.trading_day, self.session_open, self.session_close)

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> ImportSpec:
        expected = {
            "exchange",
            "symbol",
            "product",
            "timezone",
            "currency",
            "quantity_unit",
            "price_tick",
            "multiplier",
            "trading_day",
            "session_kind",
            "session_open",
            "session_close",
            "source_name",
            "source_reference",
            "availability_basis",
            "availability_note",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("data input must contain exactly the current market/session fields")
        strings: dict[str, str] = {}
        for key, item in value.items():
            maximum = 1024 if key in {"source_reference", "availability_note"} else 128
            if not isinstance(item, str) or not item or len(item) > maximum:
                raise ValueError(f"data.{key} must be a bounded nonempty string")
            strings[key] = item
        for key in ("price_tick", "multiplier"):
            if re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?", strings[key]) is None:
                raise ValueError(f"data.{key} must be a plain positive decimal string")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", strings["trading_day"]) is None:
            raise ValueError("data.trading_day must use YYYY-MM-DD")
        return cls(
            exchange=strings["exchange"],
            symbol=strings["symbol"],
            product=strings["product"],
            timezone=strings["timezone"],
            currency=strings["currency"],
            quantity_unit=strings["quantity_unit"],
            price_tick=Decimal(strings["price_tick"]),
            multiplier=Decimal(strings["multiplier"]),
            trading_day=date.fromisoformat(strings["trading_day"]),
            session_kind=strings["session_kind"],
            session_open=_utc(strings["session_open"]),
            session_close=_utc(strings["session_close"]),
            source_name=strings["source_name"],
            source_reference=strings["source_reference"],
            availability_basis=strings["availability_basis"],
            availability_note=strings["availability_note"],
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "product": self.product,
            "timezone": self.timezone,
            "currency": self.currency,
            "quantity_unit": self.quantity_unit,
            "price_tick": format(self.price_tick.normalize(), "f"),
            "multiplier": format(self.multiplier.normalize(), "f"),
            "trading_day": self.trading_day.isoformat(),
            "session_kind": self.session_kind,
            "session_open": self.session_open.isoformat().replace("+00:00", "Z"),
            "session_close": self.session_close.isoformat().replace("+00:00", "Z"),
            "source_name": self.source_name,
            "source_reference": self.source_reference,
            "availability_basis": self.availability_basis,
            "availability_note": self.availability_note,
        }


def _utc(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None:
        raise ValueError("timestamps must use UTC Z with at most six fractional second digits")
    return datetime.fromisoformat(value).astimezone(UTC)
