"""Point-in-time tests for explicit trading-session mapping."""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import TradingSession
from northstar_quant.data.catalog.time import (
    AmbiguousTradingSessionError,
    resolve_trading_day,
)

from .catalog_support import at_local, seed_synthetic_catalog


def test_night_session_maps_to_explicit_following_trading_day(db_session: Session) -> None:
    catalog = seed_synthetic_catalog(db_session)

    assert resolve_trading_day(at_local(2026, 1, 6, 21, 15), catalog.sessions) == date(2026, 1, 7)
    assert resolve_trading_day(at_local(2026, 1, 7, 1, 30), catalog.sessions) == date(2026, 1, 7)
    assert resolve_trading_day(at_local(2026, 1, 7, 2, 30), catalog.sessions) is None
    assert resolve_trading_day(at_local(2026, 1, 7, 12), catalog.sessions) is None


def test_friday_night_can_explicitly_belong_to_monday_not_saturday() -> None:
    friday_night = TradingSession(
        calendar_id=UUID("00000000-0000-0000-0000-000000000001"),
        trading_day=date(2026, 1, 12),
        sequence=0,
        kind="NIGHT",
        opens_at=at_local(2026, 1, 9, 21),
        closes_at=at_local(2026, 1, 10, 2, 30),
    )

    assert resolve_trading_day(at_local(2026, 1, 9, 21, 15), [friday_night]) == date(2026, 1, 12)


def test_naive_and_ambiguous_timestamps_fail_closed() -> None:
    first = TradingSession(
        calendar_id=UUID("00000000-0000-0000-0000-000000000001"),
        trading_day=date(2026, 1, 7),
        sequence=0,
        kind="NIGHT",
        opens_at=at_local(2026, 1, 6, 21),
        closes_at=at_local(2026, 1, 7, 2, 30),
    )
    overlapping = TradingSession(
        calendar_id=UUID("00000000-0000-0000-0000-000000000001"),
        trading_day=date(2026, 1, 7),
        sequence=1,
        kind="NIGHT",
        opens_at=at_local(2026, 1, 6, 22),
        closes_at=at_local(2026, 1, 7, 1),
    )

    with pytest.raises(ValueError, match="timezone"):
        resolve_trading_day(at_local(2026, 1, 6, 21).replace(tzinfo=None), [first])
    with pytest.raises(AmbiguousTradingSessionError):
        resolve_trading_day(at_local(2026, 1, 6, 22, 30), [first, overlapping])
