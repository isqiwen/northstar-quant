"""Behavior tests for complete-session minute quality evaluation."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    CanonicalBar,
    QualityEvaluation,
    QualityFinding,
)
from northstar_quant.data.catalog.services import CatalogCommands
from northstar_quant.data.quality.evaluations import (
    MinuteQualityEvaluationCommand,
    MinuteQualityEvaluationError,
)
from northstar_quant.data.quality.minute_service import MinuteQualityEvaluationService

from .catalog_support import SyntheticCatalog, at_local, seed_synthetic_catalog


def _quality_command(
    catalog: SyntheticCatalog,
    *,
    from_trading_day: date = date(2026, 1, 7),
    to_trading_day: date = date(2026, 1, 7),
    as_of: datetime = at_local(2026, 1, 7, 16).astimezone(UTC),
    idempotency_key: str = "minute-quality-001",
) -> MinuteQualityEvaluationCommand:
    return MinuteQualityEvaluationCommand(
        series_id=catalog.minute_series.id,
        from_trading_day=from_trading_day,
        to_trading_day=to_trading_day,
        as_of=as_of,
        idempotency_key=idempotency_key,
        correlation_id="minute-quality-test",
        causation_id="minute-quality-cause",
    )


def _slots(catalog: SyntheticCatalog) -> tuple[tuple[datetime, date], ...]:
    slots: list[tuple[datetime, date]] = []
    for trading_session in catalog.sessions:
        current = trading_session.opens_at
        while current + timedelta(minutes=1) <= trading_session.closes_at:
            slots.append((current, trading_session.trading_day))
            current += timedelta(minutes=1)
    return tuple(slots)


def _bar(
    catalog: SyntheticCatalog,
    *,
    event_time: datetime,
    trading_day: date,
    available_at: datetime | None = None,
    source_timezone_name: str = "Asia/Shanghai",
    suffix: str,
) -> CanonicalBar:
    return CanonicalBar(
        series_id=catalog.minute_series.id,
        event_time=event_time,
        trading_day=trading_day,
        available_at=available_at or (event_time + timedelta(minutes=1)),
        source_timezone_name=source_timezone_name,
        source_name="SYNTHETIC",
        source_record_id=f"minute-quality-{suffix}",
        source_content_hash="a" * 64,
        normalized_payload_hash="b" * 64,
        open_price=Decimal("3500"),
        high_price=Decimal("3510"),
        low_price=Decimal("3490"),
        close_price=Decimal("3505"),
        volume=Decimal("1"),
        turnover=Decimal("3505"),
        open_interest=Decimal("1"),
    )


def _add_complete_window(
    session: Session,
    catalog: SyntheticCatalog,
    *,
    first_available_at: datetime | None = None,
) -> tuple[CanonicalBar, ...]:
    bars = tuple(
        _bar(
            catalog,
            event_time=event_time,
            trading_day=trading_day,
            available_at=first_available_at if number == 1 else None,
            suffix=f"{number:04d}",
        )
        for number, (event_time, trading_day) in enumerate(_slots(catalog), start=1)
    )
    session.add_all(bars)
    session.commit()
    return bars


def _findings(session: Session, evaluation_id: UUID) -> tuple[QualityFinding, ...]:
    return tuple(
        session.scalars(
            select(QualityFinding)
            .where(QualityFinding.quality_evaluation_id == evaluation_id)
            .order_by(QualityFinding.rule_code)
        ).all()
    )


def test_minute_quality_persists_complete_night_and_day_coverage_and_replays(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    bars = _add_complete_window(db_session, catalog)
    service = MinuteQualityEvaluationService(db_session)
    command = _quality_command(catalog)

    first = service.evaluate(command)
    replay = service.evaluate(command)

    assert len(bars) == 570
    assert first.outcome == "PASS"
    assert first.delivery_gate == "ELIGIBLE"
    assert first.expected_observation_count == 570
    assert first.covered_observation_count == 570
    assert first.missing_observation_count == 0
    assert first.unknown_day_count == 0
    assert replay.quality_evaluation_id == first.quality_evaluation_id
    assert replay.replayed
    evaluation = db_session.get(QualityEvaluation, first.quality_evaluation_id)
    assert evaluation is not None
    assert evaluation.evaluation_scope == "MINUTE_SESSION_COVERAGE"
    assert evaluation.rule_set_name == "minute_session_coverage_quality"
    assert len(evaluation.input_fingerprint) == 64
    findings = _findings(db_session, first.quality_evaluation_id)
    assert [(item.rule_code, item.outcome, item.severity) for item in findings] == [
        ("MINUTE_SESSION_COVERAGE_COMPLETE", "PASS", "INFO")
    ]
    evidence = json.loads(findings[0].evidence)
    assert evidence["sample_trading_days"] == ["2026-01-07"]
    assert db_session.scalar(select(func.count()).select_from(QualityEvaluation)) == 1


def test_minute_quality_marks_known_complete_session_gaps_as_blocked(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    first_two_slots = _slots(catalog)[:2]
    db_session.add_all(
        _bar(
            catalog,
            event_time=event_time,
            trading_day=trading_day,
            suffix=f"partial-{number}",
        )
        for number, (event_time, trading_day) in enumerate(first_two_slots, start=1)
    )
    db_session.commit()

    result = MinuteQualityEvaluationService(db_session).evaluate(_quality_command(catalog))

    assert result.outcome == "FAIL"
    assert result.delivery_gate == "BLOCKED"
    assert result.expected_observation_count == 570
    assert result.covered_observation_count == 2
    assert result.missing_observation_count == 568
    assert [
        (item.rule_code, item.occurrence_count)
        for item in _findings(db_session, result.quality_evaluation_id)
    ] == [("MINUTE_COVERAGE_GAP", 568)]


def test_minute_quality_treats_available_after_cutoff_as_historical_absence(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    _add_complete_window(
        db_session,
        catalog,
        first_available_at=at_local(2026, 1, 8, 9),
    )

    result = MinuteQualityEvaluationService(db_session).evaluate(_quality_command(catalog))

    assert result.outcome == "FAIL"
    assert result.expected_observation_count == 570
    assert result.covered_observation_count == 569
    assert result.missing_observation_count == 1
    assert [
        (item.rule_code, item.occurrence_count)
        for item in _findings(db_session, result.quality_evaluation_id)
    ] == [("MINUTE_BAR_AVAILABLE_AFTER_CUTOFF", 1)]


def test_minute_quality_does_not_invent_a_gap_before_final_session_close(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)

    result = MinuteQualityEvaluationService(db_session).evaluate(
        _quality_command(catalog, as_of=at_local(2026, 1, 7, 10).astimezone(UTC))
    )

    assert result.outcome == "UNKNOWN"
    assert result.delivery_gate == "BLOCKED"
    assert result.expected_observation_count == 0
    assert result.covered_observation_count == 0
    assert result.missing_observation_count == 0
    assert result.unknown_day_count == 1
    assert [item.rule_code for item in _findings(db_session, result.quality_evaluation_id)] == [
        "MINUTE_AS_OF_BEFORE_FINAL_SESSION_CLOSE"
    ]


def test_minute_quality_fails_closed_for_missing_calendar_open_session_and_bad_grid(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    service = MinuteQualityEvaluationService(db_session)

    missing_calendar = service.evaluate(
        _quality_command(
            catalog,
            from_trading_day=date(2026, 1, 8),
            to_trading_day=date(2026, 1, 8),
            idempotency_key="minute-quality-calendar-unknown",
        )
    )
    no_session = service.evaluate(
        _quality_command(
            catalog,
            from_trading_day=date(2026, 1, 12),
            to_trading_day=date(2026, 1, 12),
            idempotency_key="minute-quality-session-unknown",
        )
    )
    catalog.sessions[0].opens_at += timedelta(seconds=1)
    db_session.commit()
    bad_grid = service.evaluate(
        _quality_command(catalog, idempotency_key="minute-quality-grid-unknown")
    )

    assert missing_calendar.outcome == "UNKNOWN"
    assert no_session.outcome == "UNKNOWN"
    assert bad_grid.outcome == "UNKNOWN"
    assert [
        item.rule_code for item in _findings(db_session, missing_calendar.quality_evaluation_id)
    ] == ["CALENDAR_DAY_UNKNOWN"]
    assert [item.rule_code for item in _findings(db_session, no_session.quality_evaluation_id)] == [
        "OPEN_DAY_SESSION_UNKNOWN"
    ]
    assert [item.rule_code for item in _findings(db_session, bad_grid.quality_evaluation_id)] == [
        "MINUTE_SESSION_GRID_UNKNOWN"
    ]


def test_minute_quality_persists_unknown_for_unusable_calendar_timezone(
    db_session: Session,
) -> None:
    """A malformed pinned calendar zone blocks safely without losing evidence."""

    catalog = seed_synthetic_catalog(db_session)
    catalog.calendar.timezone_name = "Not/A-Real-IANA-Zone"
    db_session.commit()

    result = MinuteQualityEvaluationService(db_session).evaluate(
        _quality_command(catalog, idempotency_key="minute-quality-calendar-timezone-unknown")
    )

    assert result.outcome == "UNKNOWN"
    assert result.delivery_gate == "BLOCKED"
    assert result.expected_observation_count == 0
    assert result.covered_observation_count == 0
    assert result.missing_observation_count == 0
    assert result.unknown_day_count == 1
    assert [item.rule_code for item in _findings(db_session, result.quality_evaluation_id)] == [
        "MINUTE_SESSION_GRID_UNKNOWN"
    ]


def test_minute_quality_rejects_closed_and_off_grid_canonical_facts(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    closed_day = date(2026, 1, 8)
    CatalogCommands().register_trading_day(
        db_session,
        calendar_id=catalog.calendar.id,
        trading_day=closed_day,
        status="CLOSED",
    )
    db_session.add_all(
        (
            _bar(
                catalog,
                event_time=at_local(2026, 1, 8, 9),
                trading_day=closed_day,
                suffix="closed",
            ),
            _bar(
                catalog,
                event_time=at_local(2026, 1, 7, 8),
                trading_day=date(2026, 1, 7),
                suffix="outside",
            ),
        )
    )
    db_session.commit()

    result = MinuteQualityEvaluationService(db_session).evaluate(
        _quality_command(
            catalog,
            from_trading_day=date(2026, 1, 7),
            to_trading_day=closed_day,
        )
    )

    assert result.outcome == "FAIL"
    assert {item.rule_code for item in _findings(db_session, result.quality_evaluation_id)} == {
        "CLOSED_DAY_HAS_MINUTE_OBSERVATION",
        "MINUTE_BAR_OUTSIDE_EXPECTED_GRID",
        "MINUTE_COVERAGE_GAP",
    }


def test_minute_quality_rejects_out_of_window_bar_at_final_session_close(
    db_session: Session,
) -> None:
    """The broad event envelope includes its upper edge for bad-fact detection."""

    catalog = seed_synthetic_catalog(db_session)
    _add_complete_window(db_session, catalog)
    db_session.add(
        _bar(
            catalog,
            event_time=at_local(2026, 1, 7, 15),
            trading_day=date(2026, 1, 6),
            suffix="out-of-window-final-close",
        )
    )
    db_session.commit()

    result = MinuteQualityEvaluationService(db_session).evaluate(_quality_command(catalog))

    assert result.outcome == "FAIL"
    assert result.delivery_gate == "BLOCKED"
    assert result.covered_observation_count == 570
    assert result.missing_observation_count == 0
    assert [item.rule_code for item in _findings(db_session, result.quality_evaluation_id)] == [
        "MINUTE_BAR_OUTSIDE_EXPECTED_GRID"
    ]


def test_minute_quality_rejects_wrong_series_and_reused_key_and_hard_bounds(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    service = MinuteQualityEvaluationService(db_session)

    with pytest.raises(MinuteQualityEvaluationError, match="one-minute BAR_START"):
        service.evaluate(
            replace(
                _quality_command(catalog),
                series_id=catalog.daily_series.id,
                idempotency_key="minute-quality-daily-series",
            )
        )
    _add_complete_window(db_session, catalog)
    first = service.evaluate(_quality_command(catalog, idempotency_key="minute-quality-replay"))
    over_cap_command = _quality_command(catalog, idempotency_key="minute-quality-over-cap")
    with pytest.raises(MinuteQualityEvaluationError, match="different intent"):
        service.evaluate(
            replace(
                _quality_command(catalog, idempotency_key="minute-quality-replay"),
                correlation_id="different-correlation",
            )
        )

    monkeypatch.setattr(
        "northstar_quant.data.quality.minute_service.MAX_MINUTE_QUALITY_EXPECTED_SLOTS", 1
    )
    db_session.rollback()
    with pytest.raises(MinuteQualityEvaluationError, match="expected-session grid input bound"):
        service.evaluate(over_cap_command)
    assert db_session.scalar(select(func.count()).select_from(QualityEvaluation)) == 1
    assert first.delivery_gate == "ELIGIBLE"


def test_minute_quality_requires_clean_session_and_non_future_cutoff(
    db_session: Session,
) -> None:
    """An evaluator must neither discard caller work nor record future visibility."""

    catalog = seed_synthetic_catalog(db_session)
    future_command = _quality_command(
        catalog,
        as_of=datetime(2099, 1, 1, tzinfo=UTC),
        idempotency_key="minute-quality-future-cutoff",
    )
    db_session.add(
        _bar(
            catalog,
            event_time=at_local(2026, 1, 6, 21),
            trading_day=date(2026, 1, 7),
            suffix="uncommitted",
        )
    )
    with pytest.raises(MinuteQualityEvaluationError, match="clean, idle dedicated"):
        MinuteQualityEvaluationService(db_session).evaluate(_quality_command(catalog))
    assert db_session.new
    db_session.rollback()

    with pytest.raises(MinuteQualityEvaluationError, match="must not be after"):
        MinuteQualityEvaluationService(db_session).evaluate(future_command)
    assert db_session.scalar(select(func.count()).select_from(QualityEvaluation)) == 0


def test_minute_quality_rejects_excessive_session_rows_before_grid_materialization(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixed session-row cap prevents adversarial calendar input expansion."""

    catalog = seed_synthetic_catalog(db_session)
    monkeypatch.setattr(
        "northstar_quant.data.quality.minute_service.MAX_MINUTE_QUALITY_SESSION_ROWS", 1
    )

    with pytest.raises(MinuteQualityEvaluationError, match="trading-session input bound"):
        MinuteQualityEvaluationService(db_session).evaluate(_quality_command(catalog))
    assert db_session.scalar(select(func.count()).select_from(QualityEvaluation)) == 0
