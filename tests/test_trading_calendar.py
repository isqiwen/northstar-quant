from datetime import UTC, date, datetime

import pytest

from northstar_quant.live import trading_calendar


def test_is_trading_session_returns_bool():
    assert isinstance(trading_calendar.is_trading_session(), bool)


def test_is_trading_session_uses_profile_calendar_and_timezone(monkeypatch):
    observed: dict[str, object] = {}

    class FakeCalendar:
        def is_session(self, session_label):
            observed["session_label"] = session_label
            return True

    class FakeCalendars:
        @staticmethod
        def get_calendar(calendar_name):
            observed["calendar_name"] = calendar_name
            return FakeCalendar()

    monkeypatch.setattr(trading_calendar, "xcals", FakeCalendars())

    result = trading_calendar.is_trading_session(
        datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        calendar="XNYS",
        timezone="America/New_York",
    )

    assert result is True
    assert observed == {
        "calendar_name": "XNYS",
        "session_label": date(2024, 1, 1),
    }


def test_real_broker_calendar_check_fails_closed_when_dependency_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(trading_calendar, "xcals", None)

    with pytest.raises(RuntimeError, match="禁止退化"):
        trading_calendar.is_trading_session(
            datetime(2024, 1, 2, 10, 0),
            calendar="XSHG",
            timezone="Asia/Shanghai",
            require_calendar=True,
        )


def test_last_trading_session_of_month_uses_exchange_calendar():
    assert trading_calendar.is_last_trading_session_of_month(
        datetime(2024, 7, 31, 15, 0),
        calendar="XSHG",
        timezone="Asia/Shanghai",
    )
    assert not trading_calendar.is_last_trading_session_of_month(
        datetime(2024, 7, 30, 15, 0),
        calendar="XSHG",
        timezone="Asia/Shanghai",
    )
