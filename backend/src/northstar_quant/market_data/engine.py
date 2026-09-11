"""Subscribed, causal market projections shared by every trading environment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from . import MarketBar
from .window import MarketWindow


@dataclass(frozen=True, slots=True)
class BarStream:
    contract_id: UUID
    interval_seconds: int
    source_scope: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.contract_id, UUID)
            or type(self.interval_seconds) is not int
            or self.interval_seconds <= 0
            or not isinstance(self.source_scope, str)
        ):
            raise ValueError("bar stream requires a canonical contract, interval and source")


@dataclass(frozen=True, slots=True)
class MarketFrame:
    stream: BarStream
    bars: tuple[MarketBar, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class DataEngine:
    """Immutable candidate state: commit it with the rest of the core event.

    Streams distinguish instruments, intervals and fixed sources. Each keeps only
    subscribed warmup; duplicate identities never dispatch a second observation.
    An adapter supplies completed bars and their explicit trading day. No gap,
    settlement price, late correction or missing minute is fabricated here.
    """

    _windows: tuple[tuple[BarStream, MarketWindow], ...] = ()
    _at: datetime | None = None

    def subscribe(
        self, stream: BarStream, capacity: int, *, history: tuple[MarketBar, ...] = ()
    ) -> DataEngine:
        if any(key == stream for key, _ in self._windows):
            if self.window(stream).capacity == capacity and not history:
                return self
            raise ValueError("an active stream cannot replace its fixed warmup binding")
        window = MarketWindow(capacity, stream.interval_seconds, history)
        latest = history[-1].available_at if history else self._at
        at = max(self._at, latest) if self._at and latest else latest
        return DataEngine((*self._windows, (stream, window)), at)

    def unsubscribe(self, stream: BarStream) -> DataEngine:
        self.window(stream)
        return DataEngine(tuple(item for item in self._windows if item[0] != stream), self._at)

    def window(self, stream: BarStream) -> MarketWindow:
        for key, window in self._windows:
            if key == stream:
                return window
        raise ValueError("market stream has no active subscription")

    def accepts(self, stream: BarStream, bar: MarketBar) -> bool:
        return self.window(stream).accepts(bar)

    def advance(
        self, stream: BarStream, bar: MarketBar, *, at: datetime
    ) -> tuple[DataEngine, MarketFrame | None]:
        window = self.window(stream)
        if not window.accepts(bar):
            return self, None
        if not isinstance(at, datetime) or at.utcoffset() != timedelta(0):
            raise ValueError("market clock must be aware UTC")
        if self._at is not None and at < self._at:
            raise ValueError("market engine clock cannot move backwards")
        updated = window.append(bar, at=at)
        candidate = DataEngine(
            tuple((key, updated if key == stream else value) for key, value in self._windows), at
        )
        return candidate, MarketFrame(stream, updated.bars, at)
