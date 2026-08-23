"""P1-WP04 交易日历公共边界与 fail-closed 契约。"""

from __future__ import annotations

import ast
from dataclasses import is_dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import northstar_quant.data.calendars as calendars
from northstar_quant.data.calendars import (
    CalendarError,
    CalendarQualityStatus,
    CalendarSession,
    TradingCalendarSnapshot,
    load_trading_calendar,
    load_trading_calendar_payload,
)
from tests.helpers.paths import PROJECT_ROOT


FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "golden" / "trading_calendar" / "cn_futures_synthetic_v1.yaml"
)
CALENDAR_SOURCE_FILES = tuple(
    PROJECT_ROOT / "src" / "northstar_quant" / "data" / "calendars" / name
    for name in ("models.py", "loader.py", "service.py")
)
PUBLIC_VALUE_TYPES = frozenset(
    {
        "CalendarSession",
        "TradingCalendarSnapshot",
        "CalendarDecision",
    }
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _aware(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=SHANGHAI)


def _single_day_snapshot(*, sessions: tuple[CalendarSession, ...]) -> TradingCalendarSnapshot:
    return TradingCalendarSnapshot.create(
        calendar_id="TEST_CALENDAR",
        exchange_id="SHFE",
        timezone_name="Asia/Shanghai",
        observed_at=_aware(date(2026, 1, 1), 8),
        available_at=_aware(date(2026, 1, 1), 9),
        coverage_start=date(2026, 1, 2),
        coverage_end=date(2026, 1, 2),
        source_artifact_hash="0" * 64,
        quality_status=CalendarQualityStatus.PASS,
        trading_days=(date(2026, 1, 2),),
        closed_dates=(),
        sessions=sessions,
    )


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "calendar.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_calendar_public_value_types_are_frozen_and_exported() -> None:
    assert PUBLIC_VALUE_TYPES <= set(calendars.__all__)
    for name in PUBLIC_VALUE_TYPES:
        model = getattr(calendars, name)
        assert is_dataclass(model), f"{name} 必须是 dataclass 值对象"
        assert model.__dataclass_params__.frozen, f"{name} 必须不可变"


def test_calendar_core_is_offline_broker_neutral_and_does_not_read_current_time() -> None:
    for source_path in CALENDAR_SOURCE_FILES:
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        forbidden = {
            module
            for module in imported_modules
            if module.startswith("northstar_quant.trading_execution")
            or module.startswith("northstar_quant.application")
            or module.startswith("exchange_calendars")
        }

        assert not forbidden, source_path
        assert "datetime.now" not in source
        assert "httpx" not in source


def test_golden_calendar_is_explicitly_test_only_and_cannot_load_by_default() -> None:
    source = FIXTURE_PATH.read_text(encoding="utf-8")

    assert "fixture_scope: test_only" in source
    assert "不是交易所权威日历" in source
    with pytest.raises(CalendarError, match="allow_test_fixtures=True"):
        load_trading_calendar(FIXTURE_PATH)


def test_loader_rejects_duplicate_and_unknown_yaml_fields(tmp_path: Path) -> None:
    source = FIXTURE_PATH.read_text(encoding="utf-8")
    duplicate = _write(tmp_path, source.replace("version: 1", "version: 1\nversion: 1", 1))

    with pytest.raises(CalendarError, match="重复字段"):
        load_trading_calendar(duplicate, allow_test_fixtures=True)

    unknown = _write(tmp_path, source.replace("version: 1", "version: 1\nunknown: true", 1))
    with pytest.raises(CalendarError, match="未知字段"):
        load_trading_calendar(unknown, allow_test_fixtures=True)


def test_loader_rejects_naive_time_missing_source_and_out_of_coverage_dates(tmp_path: Path) -> None:
    source = FIXTURE_PATH.read_text(encoding="utf-8")
    naive = _write(
        tmp_path,
        source.replace(
            'observed_at: "2025-12-20T08:00:00+08:00"',
            'observed_at: "2025-12-20T08:00:00"',
            1,
        ),
    )
    with pytest.raises(CalendarError, match="含时区"):
        load_trading_calendar(naive, allow_test_fixtures=True)

    no_source = _write(
        tmp_path,
        source.replace(
            "source_artifact_hash: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "source_artifact_hash: ''",
            1,
        ),
    )
    with pytest.raises(CalendarError, match="非空字符串"):
        load_trading_calendar(no_source, allow_test_fixtures=True)

    out_of_coverage = _write(
        tmp_path,
        source.replace(
            "    trading_days:\n      - 2025-12-31",
            "    trading_days:\n      - 2026-01-06",
            1,
        ),
    )
    with pytest.raises(CalendarError, match="超出 coverage"):
        load_trading_calendar(out_of_coverage, allow_test_fixtures=True)


def test_overlapping_absolute_sessions_fail_closed_in_the_snapshot_model() -> None:
    day = date(2026, 1, 2)
    first = CalendarSession(
        exchange_id="SHFE",
        instrument_id="SHFE.RB",
        trading_day=day,
        session_id="ONE",
        opens_at=_aware(day, 9),
        closes_at=_aware(day, 12),
    )
    second = CalendarSession(
        exchange_id="SHFE",
        instrument_id="SHFE.RB",
        trading_day=day,
        session_id="TWO",
        opens_at=_aware(day, 11),
        closes_at=_aware(day, 15),
    )

    with pytest.raises(CalendarError, match="不能重叠"):
        _single_day_snapshot(sessions=(first, second))


def test_calendar_rejects_actual_contract_as_instrument_identity() -> None:
    with pytest.raises(CalendarError, match="稳定品种身份"):
        CalendarSession(
            exchange_id="SHFE",
            instrument_id="SHFE.RB2610",
            trading_day=date(2026, 1, 2),
            session_id="DAY",
            opens_at=_aware(date(2026, 1, 2), 9),
            closes_at=_aware(date(2026, 1, 2), 15),
        )


def test_calendar_session_rejects_instrument_from_a_different_exchange() -> None:
    with pytest.raises(CalendarError, match="稳定品种身份"):
        CalendarSession(
            exchange_id="DCE",
            instrument_id="SHFE.RB",
            trading_day=date(2026, 1, 2),
            session_id="DAY",
            opens_at=_aware(date(2026, 1, 2), 9),
            closes_at=_aware(date(2026, 1, 2), 15),
        )


def test_runtime_calendar_file_is_rejected_and_only_immutable_payload_mode_can_parse_it(
    tmp_path: Path,
) -> None:
    runtime = _write(
        tmp_path,
        FIXTURE_PATH.read_text(encoding="utf-8").replace(
            "fixture_scope: test_only",
            "fixture_scope: runtime",
            1,
        ),
    )

    with pytest.raises(CalendarError, match="不可变制品 payload"):
        load_trading_calendar(runtime)
    snapshots = load_trading_calendar_payload(
        runtime.read_bytes(),
        require_runtime=True,
    )
    assert snapshots
