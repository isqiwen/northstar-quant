"""P1-WP04 中国商品期货日历事实的离线行为测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from northstar_quant.data.calendars import (
    CalendarDecisionStatus,
    CalendarError,
    CalendarService,
    load_trading_calendar,
)
from tests.helpers.paths import PROJECT_ROOT


FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "golden" / "trading_calendar" / "cn_futures_synthetic_v1.yaml"
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SHANGHAI)


def _service() -> CalendarService:
    return CalendarService(load_trading_calendar(FIXTURE_PATH, allow_test_fixtures=True))


def test_synthetic_golden_fixture_replays_with_stable_content_hashes() -> None:
    """同一离线 fixture 的重载不依赖网络或当前时间。"""

    first = load_trading_calendar(FIXTURE_PATH, allow_test_fixtures=True)
    second = load_trading_calendar(FIXTURE_PATH, allow_test_fixtures=True)

    assert [item.snapshot_hash for item in first] == [item.snapshot_hash for item in second]
    assert all(item.quality_status.value == "pass" for item in first)
    assert len(first) == 4


def test_weekend_and_spring_festival_long_holiday_are_explicitly_closed() -> None:
    service = _service()
    decision_at = _local(2026, 2, 10, 10)

    spring_festival = service.is_trading_day("SHFE", "SHFE.RB", date(2026, 2, 20), decision_at)
    weekend = service.is_trading_day("SHFE", "SHFE.RB", date(2026, 10, 10), _local(2026, 10, 1, 10))

    assert spring_festival.status is CalendarDecisionStatus.CLOSED
    assert spring_festival.reason_code == "EXCHANGE_CLOSED_DATE"
    assert weekend.status is CalendarDecisionStatus.CLOSED
    assert weekend.reason_code == "EXCHANGE_CLOSED_DATE"


def test_night_session_crosses_civil_day_but_keeps_explicit_trading_day() -> None:
    service = _service()
    decision = service.resolve_market_session(
        "SHFE",
        "SHFE.RB",
        _local(2026, 1, 4, 21, 15),
        _local(2026, 1, 5, 10),
    )
    after_night = service.resolve_market_session(
        "SHFE",
        "SHFE.RB",
        _local(2026, 1, 5, 3),
        _local(2026, 1, 5, 10),
    )

    assert decision.status is CalendarDecisionStatus.OPEN
    assert decision.trading_day == date(2026, 1, 5)
    assert decision.session is not None
    assert decision.session.session_id == "NIGHT"
    assert after_night.status is CalendarDecisionStatus.CLOSED
    assert after_night.reason_code == "OUTSIDE_DECLARED_SESSION"


def test_friday_night_can_explicitly_belong_to_following_monday_trading_day() -> None:
    service = _service()

    decision = service.resolve_market_session(
        "SHFE",
        "SHFE.RB",
        _local(2026, 1, 2, 21, 15),
        _local(2026, 1, 5, 10),
    )

    assert decision.status is CalendarDecisionStatus.OPEN
    assert decision.trading_day == date(2026, 1, 5)
    assert decision.session is not None
    assert decision.session.session_id == "FRIDAY_NIGHT"


def test_cross_year_closure_and_next_day_do_not_use_weekday_inference() -> None:
    service = _service()
    decision_at = _local(2026, 1, 5, 10)

    new_year = service.is_trading_day("SHFE", "SHFE.RB", date(2026, 1, 1), decision_at)
    next_day = service.next_trading_day("SHFE", "SHFE.RB", date(2025, 12, 31), decision_at)

    assert new_year.status is CalendarDecisionStatus.CLOSED
    assert next_day.status is CalendarDecisionStatus.OPEN
    assert next_day.reason_code == "NEXT_TRADING_DAY_RESOLVED"
    assert next_day.trading_day == date(2026, 1, 5)


def test_reopen_after_long_holiday_only_uses_explicit_night_session() -> None:
    service = _service()
    decision = service.resolve_market_session(
        "SHFE",
        "SHFE.RB",
        _local(2026, 2, 23, 21, 30),
        _local(2026, 2, 24, 9),
    )

    assert decision.status is CalendarDecisionStatus.OPEN
    assert decision.trading_day == date(2026, 2, 24)
    assert decision.reason_code == "MARKET_SESSION_OPEN"


def test_early_close_and_product_session_difference_are_exact() -> None:
    service = _service()
    decision_at = _local(2026, 10, 9, 15, 30)
    rb = service.resolve_market_session(
        "SHFE",
        "SHFE.RB",
        _local(2026, 10, 9, 14),
        decision_at,
    )
    au = service.resolve_market_session(
        "SHFE",
        "SHFE.AU",
        _local(2026, 10, 9, 14),
        decision_at,
    )

    assert rb.status is CalendarDecisionStatus.CLOSED
    assert rb.reason_code == "OUTSIDE_DECLARED_SESSION"
    assert au.status is CalendarDecisionStatus.OPEN
    assert au.session is not None
    assert au.session.session_id == "DAY"


def test_missing_instrument_session_is_unknown_not_assumed_closed_or_open() -> None:
    service = _service()

    decision = service.is_trading_day(
        "SHFE",
        "SHFE.CU",
        date(2026, 10, 9),
        _local(2026, 10, 9, 10),
    )

    assert decision.status is CalendarDecisionStatus.UNKNOWN
    assert decision.reason_code == "INSTRUMENT_SESSION_UNKNOWN"
    assert not decision.is_open


@pytest.mark.parametrize("instrument_id", ("RB2610", "SHFE.RB2610", "RB2610.SHFE"))
def test_calendar_rejects_actual_month_contract_identity_instead_of_guessing_product(
    instrument_id: str,
) -> None:
    service = _service()

    with pytest.raises(CalendarError, match="稳定品种身份"):
        service.is_trading_day(
            "SHFE",
            instrument_id,
            date(2026, 10, 9),
            _local(2026, 10, 9, 10),
        )


def test_calendar_rejects_stable_instrument_from_a_different_exchange() -> None:
    service = _service()

    with pytest.raises(CalendarError, match="稳定品种身份"):
        service.is_trading_day(
            "DCE",
            "SHFE.RB",
            date(2026, 10, 9),
            _local(2026, 10, 9, 10),
        )


def test_future_available_calendar_never_leaks_into_earlier_pit_decision() -> None:
    service = _service()
    before_available_at = datetime(2026, 12, 1, 0, tzinfo=UTC)
    before_available = service.is_trading_day(
        "SHFE",
        "SHFE.RB",
        date(2026, 12, 31),
        before_available_at,
    )
    after_available = service.is_trading_day(
        "SHFE",
        "SHFE.RB",
        date(2026, 12, 31),
        _local(2026, 12, 16, 10),
    )

    assert before_available.status is CalendarDecisionStatus.NOT_YET_AVAILABLE
    assert before_available.reason_code == "CALENDAR_AVAILABLE_AFTER_DECISION_TIME"
    assert before_available.snapshot_hash is None
    assert not hasattr(service, "snapshots")
    visible = service.snapshots_as_of(before_available_at)
    assert all(item.available_at <= before_available_at for item in visible)
    assert {
        item.calendar_id for item in visible
    } == {
        "CN_FUTURES_SYNTHETIC_YEAR_END_V1",
        "CN_FUTURES_SYNTHETIC_SPRING_FESTIVAL_V1",
        "CN_FUTURES_SYNTHETIC_NATIONAL_DAY_V1",
    }
    assert after_available.status is CalendarDecisionStatus.OPEN


def test_next_trading_day_never_scans_beyond_the_selected_snapshot_coverage() -> None:
    service = _service()

    decision = service.next_trading_day(
        "SHFE",
        "SHFE.RB",
        date(2026, 10, 12),
        _local(2026, 10, 12, 16),
    )

    assert decision.status is CalendarDecisionStatus.UNKNOWN
    assert decision.reason_code == "NEXT_TRADING_DAY_OUTSIDE_COVERAGE"
