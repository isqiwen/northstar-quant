"""Behavior tests for read-only quality revalidation."""

from __future__ import annotations

from datetime import UTC, date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import CanonicalBar
from northstar_quant.data.quality.applicability_service import QualityApplicabilityService
from northstar_quant.data.quality.evaluations import (
    DailyQualityEvaluationCommand,
    MinuteQualityEvaluationCommand,
    QualityApplicabilityResult,
)
from northstar_quant.data.quality.minute_service import MinuteQualityEvaluationService
from northstar_quant.data.quality.service import DailyQualityEvaluationService

from .catalog_support import SyntheticCatalog, at_local, seed_synthetic_catalog


def _bar(catalog: SyntheticCatalog) -> CanonicalBar:
    return CanonicalBar(
        series_id=catalog.minute_series.id,
        event_time=at_local(2026, 1, 6, 21),
        trading_day=date(2026, 1, 7),
        available_at=at_local(2026, 1, 6, 21, 1),
        source_timezone_name="Asia/Shanghai",
        source_name="SYNTHETIC",
        source_record_id="applicability-minute-0001",
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


def _command(catalog: SyntheticCatalog) -> MinuteQualityEvaluationCommand:
    return MinuteQualityEvaluationCommand(
        series_id=catalog.minute_series.id,
        from_trading_day=date(2026, 1, 7),
        to_trading_day=date(2026, 1, 7),
        as_of=at_local(2026, 1, 7, 16).astimezone(UTC),
        idempotency_key="applicability-minute-001",
        correlation_id="applicability-test",
    )


def _daily_command(catalog: SyntheticCatalog) -> DailyQualityEvaluationCommand:
    return DailyQualityEvaluationCommand(
        series_id=catalog.daily_series.id,
        from_trading_day=date(2026, 1, 7),
        to_trading_day=date(2026, 1, 7),
        as_of=at_local(2026, 1, 7, 16).astimezone(UTC),
        idempotency_key="applicability-daily-001",
        correlation_id="applicability-test",
    )


def _assess_in_fresh_session(
    db_session: Session, quality_evaluation_id: UUID
) -> QualityApplicabilityResult:
    """Use the service's required dedicated idle session boundary."""

    with Session(
        db_session.get_bind(), autoflush=False, expire_on_commit=False
    ) as assessment_session:
        return QualityApplicabilityService(assessment_session).assess(quality_evaluation_id)


def test_quality_applicability_detects_calendar_or_canonical_input_drift(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    evaluated = MinuteQualityEvaluationService(db_session).evaluate(_command(catalog))

    current = _assess_in_fresh_session(db_session, evaluated.quality_evaluation_id)

    assert current.applicable
    assert current.delivery_gate == "BLOCKED"
    assert current.reason_code == "QUALITY_EVALUATION_INPUT_CURRENT"

    db_session.add(_bar(catalog))
    db_session.commit()

    changed = _assess_in_fresh_session(db_session, evaluated.quality_evaluation_id)

    assert not changed.applicable
    assert changed.delivery_gate == "BLOCKED"
    assert changed.reason_code == "REVALIDATION_REQUIRED"


def test_quality_applicability_revalidates_daily_inputs(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    evaluated = DailyQualityEvaluationService(db_session).evaluate(_daily_command(catalog))

    assert _assess_in_fresh_session(db_session, evaluated.quality_evaluation_id).applicable

    db_session.add(
        CanonicalBar(
            series_id=catalog.daily_series.id,
            event_time=at_local(2026, 1, 6, 21),
            trading_day=date(2026, 1, 7),
            available_at=at_local(2026, 1, 7, 15),
            source_timezone_name="Asia/Shanghai",
            source_name="SYNTHETIC",
            source_record_id="applicability-daily-0001",
            source_content_hash="c" * 64,
            normalized_payload_hash="d" * 64,
            open_price=Decimal("3500"),
            high_price=Decimal("3510"),
            low_price=Decimal("3490"),
            close_price=Decimal("3505"),
            volume=Decimal("1"),
            turnover=Decimal("3505"),
            open_interest=Decimal("1"),
        )
    )
    db_session.commit()

    changed = _assess_in_fresh_session(db_session, evaluated.quality_evaluation_id)

    assert not changed.applicable
    assert changed.reason_code == "REVALIDATION_REQUIRED"
