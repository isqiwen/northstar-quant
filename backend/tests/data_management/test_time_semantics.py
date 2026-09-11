"""Point-in-time tests for explicit trading-session mapping."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from northstar_quant.market_data.sessions import (
    AmbiguousTradingSessionError,
    SessionWindow,
    resolve_trading_day,
)

from .catalog_support import at_local, seed_synthetic_catalog


def test_night_session_maps_to_explicit_following_trading_day(db_session: Session) -> None:
    catalog = seed_synthetic_catalog(db_session)
    sessions = tuple(
        SessionWindow(s.trading_day, s.opens_at, s.closes_at) for s in catalog.sessions
    )

    assert resolve_trading_day(at_local(2026, 1, 6, 21, 15), sessions) == date(2026, 1, 7)
    assert resolve_trading_day(at_local(2026, 1, 7, 1, 30), sessions) == date(2026, 1, 7)
    assert resolve_trading_day(at_local(2026, 1, 7, 2, 30), sessions) is None
    assert resolve_trading_day(at_local(2026, 1, 7, 12), sessions) is None


def test_friday_night_can_explicitly_belong_to_monday_not_saturday() -> None:
    friday_night = SessionWindow(
        trading_day=date(2026, 1, 12),
        opens_at=at_local(2026, 1, 9, 21),
        closes_at=at_local(2026, 1, 10, 2, 30),
    )

    assert resolve_trading_day(at_local(2026, 1, 9, 21, 15), [friday_night]) == date(2026, 1, 12)


def test_naive_and_ambiguous_timestamps_fail_closed() -> None:
    first = SessionWindow(
        trading_day=date(2026, 1, 7),
        opens_at=at_local(2026, 1, 6, 21),
        closes_at=at_local(2026, 1, 7, 2, 30),
    )
    overlapping = SessionWindow(
        trading_day=date(2026, 1, 7),
        opens_at=at_local(2026, 1, 6, 22),
        closes_at=at_local(2026, 1, 7, 1),
    )

    with pytest.raises(ValueError, match="timezone"):
        resolve_trading_day(at_local(2026, 1, 6, 21).replace(tzinfo=None), [first])
    with pytest.raises(AmbiguousTradingSessionError):
        resolve_trading_day(at_local(2026, 1, 6, 22, 30), [first, overlapping])
