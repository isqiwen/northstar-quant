"""P4-WP12 point-in-time event-study records for research, never signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import math
import re


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class EventStudyError(ValueError):
    pass


class EventWindow(StrEnum):
    T_PLUS_15_MINUTES = "T+15m"
    T_PLUS_1_HOUR = "T+1h"
    T_PLUS_4_HOURS = "T+4h"
    T_PLUS_1_DAY = "T+1d"
    T_PLUS_3_DAYS = "T+3d"
    T_PLUS_5_DAYS = "T+5d"

    @property
    def duration(self) -> timedelta:
        return {
            EventWindow.T_PLUS_15_MINUTES: timedelta(minutes=15),
            EventWindow.T_PLUS_1_HOUR: timedelta(hours=1),
            EventWindow.T_PLUS_4_HOURS: timedelta(hours=4),
            EventWindow.T_PLUS_1_DAY: timedelta(days=1),
            EventWindow.T_PLUS_3_DAYS: timedelta(days=3),
            EventWindow.T_PLUS_5_DAYS: timedelta(days=5),
        }[self]


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise EventStudyError(f"{field} must be a non-empty identifier")
    return value.strip()


def _time(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EventStudyError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class EventStudyResult:
    study_id: str
    event_id: str
    dataset_version: str
    window: EventWindow
    event_time: datetime
    window_end: datetime
    available_at: datetime
    return_value: float
    volatility: float
    volume: float
    open_interest: float
    spread: float
    basis: float
    mfe: float
    mae: float

    def __post_init__(self) -> None:
        for field in ("study_id", "event_id", "dataset_version"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if not isinstance(self.window, EventWindow):
            raise EventStudyError("window must be a supported EventWindow")
        event_time = _time(self.event_time, "event_time")
        window_end = _time(self.window_end, "window_end")
        available_at = _time(self.available_at, "available_at")
        if window_end != event_time + self.window.duration:
            raise EventStudyError("window_end must match the declared event-study window")
        if available_at < window_end:
            raise EventStudyError("available_at cannot precede completion of the study window")
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "window_end", window_end)
        object.__setattr__(self, "available_at", available_at)
        for field in ("return_value", "volatility", "volume", "open_interest", "spread", "basis", "mfe", "mae"):
            value = getattr(self, field)
            if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value):
                raise EventStudyError(f"{field} must be a finite numeric observation")
            object.__setattr__(self, field, float(value))
        if any(getattr(self, field) < 0 for field in ("volatility", "volume", "open_interest", "mfe")):
            raise EventStudyError("volatility, volume, open_interest and MFE cannot be negative")
        if self.mae > 0:
            raise EventStudyError("MAE cannot be positive")

    def is_available_at(self, simulation_time: datetime) -> bool:
        return self.available_at <= _time(simulation_time, "simulation_time")


def event_study_as_of(*, result: EventStudyResult, simulation_time: datetime) -> EventStudyResult:
    """Expose completed study results only after they became knowable."""
    if not isinstance(result, EventStudyResult):
        raise EventStudyError("result must be typed")
    if not result.is_available_at(simulation_time):
        raise EventStudyError("event study result is not yet available at simulation_time")
    return result


__all__ = ["EventStudyError", "EventStudyResult", "EventWindow", "event_study_as_of"]
