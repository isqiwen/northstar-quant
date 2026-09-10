"""Immutable bounded market projection for causal strategy inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import MarketBar


@dataclass(frozen=True, slots=True)
class MarketWindow:
    """Replace after successful processing; never mutate a shared candidate window."""

    capacity: int
    interval_seconds: int
    bars: tuple[MarketBar, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.capacity) is not int
            or self.capacity <= 0
            or type(self.interval_seconds) is not int
            or self.interval_seconds <= 0
            or not isinstance(self.bars, tuple)
            or len(self.bars) > self.capacity
        ):
            raise ValueError("market window requires positive bounded capacity and interval")
        previous: tuple[MarketBar, ...] = ()
        for bar in self.bars:
            if not self._accepts(bar, previous):
                raise ValueError("market checkpoint repeats an observation")
            previous += (bar,)

    def _accepts(self, bar: MarketBar, history: tuple[MarketBar, ...]) -> bool:
        bar.validate(interval_seconds=self.interval_seconds)
        for previous in history:
            if bar.observation_id == previous.observation_id:
                if previous != bar:
                    raise ValueError("observation identity was reused with different facts")
                return False
        if history and (
            bar.event_time <= history[-1].event_time or bar.available_at < history[-1].available_at
        ):
            raise ValueError("market window rejects late or revised bars")
        if history and bar.event_time < history[-1].completed_at:
            raise ValueError("market window rejects overlapping bar intervals")
        if history and bar.trading_day < history[-1].trading_day:
            raise ValueError("market window rejects decreasing trading days")
        return True

    def accepts(self, bar: MarketBar) -> bool:
        return self._accepts(bar, self.bars)

    def append(self, bar: MarketBar, *, at: datetime) -> MarketWindow:
        if not isinstance(at, datetime) or at.utcoffset() != timedelta(0):
            raise ValueError("market clock must be aware UTC")
        if at < bar.available_at:
            raise ValueError("market clock precedes input availability")
        if not self.accepts(bar):
            return self
        # Construct without revalidating the entire already verified history.
        result = object.__new__(MarketWindow)
        object.__setattr__(result, "capacity", self.capacity)
        object.__setattr__(result, "interval_seconds", self.interval_seconds)
        object.__setattr__(result, "bars", (*self.bars, bar)[-self.capacity :])
        return result
