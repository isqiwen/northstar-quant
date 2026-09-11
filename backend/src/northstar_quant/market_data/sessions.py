"""Point-in-time trading-day resolution from explicit session instances."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime


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
