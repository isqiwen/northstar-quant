"""Immutable market values shared by data adapters and trading calculations."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Market:
    contract_id: UUID
    symbol: str
    exchange_timezone: str
    currency: str
    quantity_unit: str
    price_tick: Decimal
    multiplier: Decimal
    interval_seconds: int


@dataclass(frozen=True, slots=True)
class MarketBar:
    observation_id: UUID
    event_time: datetime
    completed_at: datetime
    available_at: datetime
    trading_day: date
    close: Decimal
    volume: Decimal

    def validate(self, *, interval_seconds: int, price_tick: Decimal | None = None) -> None:
        bar = self
        if not isinstance(bar, MarketBar) or not isinstance(bar.observation_id, UUID):
            raise ValueError("research requires canonical observations")
        for at in (bar.event_time, bar.completed_at, bar.available_at):
            if not isinstance(at, datetime) or at.utcoffset() != timedelta(0):
                raise ValueError("bar times must be aware UTC")
        if (
            bar.completed_at != bar.event_time + timedelta(seconds=interval_seconds)
            or bar.available_at < bar.completed_at
        ):
            raise ValueError("bar availability cannot precede its declared completion")
        if type(bar.trading_day) is not date:
            raise ValueError("bar trading_day must be explicit")
        if not isinstance(bar.close, Decimal) or not bar.close.is_finite() or bar.close <= 0:
            raise ValueError("bar close must be a positive Decimal")
        exponent = bar.close.as_tuple().exponent
        if (
            not isinstance(exponent, int)
            or exponent < -18
            or len(bar.close.as_tuple().digits) > 34
            or bar.close.adjusted() > 33
        ):
            raise ValueError("bar close exceeds the bounded 34-digit/18-place financial domain")
        if not isinstance(bar.volume, Decimal) or not bar.volume.is_finite() or bar.volume < 0:
            raise ValueError("bar volume must be a nonnegative Decimal")
        volume_exponent = bar.volume.as_tuple().exponent
        if (
            not isinstance(volume_exponent, int)
            or volume_exponent < -18
            or len(bar.volume.as_tuple().digits) > 34
            or bar.volume.adjusted() > 33
        ):
            raise ValueError("bar volume exceeds the bounded observation domain")
        if price_tick is not None:
            numerator, denominator = bar.close.as_integer_ratio()
            tick_numerator, tick_denominator = price_tick.as_integer_ratio()
            if (numerator * tick_denominator) % (denominator * tick_numerator):
                raise ValueError("bar close must be tick aligned")
