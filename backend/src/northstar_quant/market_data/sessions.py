"""Point-in-time trading-day resolution from explicit session instances."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """One declared interval; it does not invent or certify exchange holidays."""

    trading_day: date
    opens_at: datetime
    closes_at: datetime

    def __post_init__(self) -> None:
        if type(self.trading_day) is not date:
            raise ValueError("session trading day must be an explicit date")
        for at in (self.opens_at, self.closes_at):
            if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
                raise ValueError("session bounds must include a timezone offset")
        if self.opens_at >= self.closes_at:
            raise ValueError("session opening must precede its close")


class AmbiguousTradingSessionError(ValueError):
    """Raised when incorrectly overlapping sessions would produce two trading days."""


@dataclass(frozen=True, slots=True)
class SessionSchedule:
    """Fixed, sourced session instances; declarations never grant execution authority."""

    source_reference: str
    available_at: datetime
    windows: tuple[SessionWindow, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_reference, str) or not 1 <= len(self.source_reference) <= 500:
            raise ValueError("session schedule requires its bounded source reference")
        if not isinstance(
            self.available_at, datetime
        ) or self.available_at.utcoffset() != timedelta(0):
            raise ValueError("session schedule availability must use UTC")
        if not isinstance(self.windows, tuple) or not 1 <= len(self.windows) <= 64:
            raise ValueError("session schedule requires 1..64 fixed windows")
        for index, window in enumerate(self.windows):
            if not isinstance(window, SessionWindow):
                raise ValueError("session schedule requires explicit windows")
            if any(at.second or at.microsecond for at in (window.opens_at, window.closes_at)):
                raise ValueError("observed-minute sessions require minute-aligned bounds")
            if index and (
                self.windows[index - 1].closes_at > window.opens_at
                or self.windows[index - 1].trading_day > window.trading_day
            ):
                raise ValueError("session schedule must be ordered without overlapping windows")

    def to_dict(self) -> dict[str, object]:
        return {
            "source_reference": self.source_reference,
            "available_at": self.available_at.isoformat(),
            "windows": [
                {
                    "trading_day": window.trading_day.isoformat(),
                    "opens_at": window.opens_at.isoformat(),
                    "closes_at": window.closes_at.isoformat(),
                }
                for window in self.windows
            ],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SessionSchedule":
        try:
            if set(value) != {"source_reference", "available_at", "windows"} or (
                not isinstance(value["windows"], list)
                or not 1 <= len(value["windows"]) <= 64
                or any(
                    not isinstance(row, dict)
                    or set(row) != {"trading_day", "opens_at", "closes_at"}
                    for row in value["windows"]
                )
            ):
                raise ValueError("invalid fixed session schedule fields")
            return cls(
                value["source_reference"],
                datetime.fromisoformat(value["available_at"]),
                tuple(
                    SessionWindow(
                        date.fromisoformat(row["trading_day"]),
                        datetime.fromisoformat(row["opens_at"]),
                        datetime.fromisoformat(row["closes_at"]),
                    )
                    for row in value["windows"]
                ),
            )
        except (KeyError, TypeError) as error:
            raise ValueError("invalid fixed session schedule") from error


def resolve_trading_day(event_time: datetime, sessions: Iterable[SessionWindow]) -> date | None:
    """Map an aware timestamp to the explicit trading day of its session.

    Session intervals are half-open: an instant exactly at ``opens_at`` is in the
    session, while an instant exactly at ``closes_at`` is not.  No arithmetic on
    civil dates is used, so Friday night can correctly map to the following Monday
    and exchange holidays remain explicit.
    """

    if event_time.tzinfo is None or event_time.utcoffset() is None:
        raise ValueError("event_time must include a timezone offset")

    matches = [
        session for session in sessions if session.opens_at <= event_time < session.closes_at
    ]
    if len(matches) > 1:
        raise AmbiguousTradingSessionError("more than one session contains the event time")
    return matches[0].trading_day if matches else None
