"""Point-in-time trading-day resolution from explicit session instances."""

from collections.abc import Iterable
from datetime import date, datetime

from northstar_quant.data.catalog.models import TradingSession


class AmbiguousTradingSessionError(ValueError):
    """Raised when incorrectly overlapping sessions would produce two trading days."""


def resolve_trading_day(event_time: datetime, sessions: Iterable[TradingSession]) -> date | None:
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
