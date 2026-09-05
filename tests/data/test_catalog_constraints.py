"""Catalog identity, precision, and conflict-policy behavior tests."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import CanonicalBar
from northstar_quant.data.catalog.services import CatalogCommands

from .catalog_support import at_local, seed_synthetic_catalog


def _bar(*, series_id: UUID, event_time_hour: int = 9) -> CanonicalBar:
    event_time = at_local(2026, 1, 7, event_time_hour)
    return CanonicalBar(
        series_id=series_id,
        event_time=event_time,
        trading_day=date(2026, 1, 7),
        available_at=event_time,
        source_timezone_name="Asia/Shanghai",
        source_name="SYNTHETIC",
        source_record_id=f"record-{event_time_hour}",
        source_content_hash="a" * 64,
        normalized_payload_hash="b" * 64,
        open_price=Decimal("100.10"),
        high_price=Decimal("102.00"),
        low_price=Decimal("99.90"),
        close_price=Decimal("101.20"),
        volume=Decimal("42"),
    )


def test_duplicate_canonical_bar_is_rejected_without_rewriting_history(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    db_session.add(_bar(series_id=catalog.minute_series.id))
    db_session.commit()

    duplicate = _bar(series_id=catalog.minute_series.id)
    duplicate.close_price = Decimal("999.99")
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_instant_is_valid_for_two_different_series(db_session: Session) -> None:
    catalog = seed_synthetic_catalog(db_session)
    db_session.add(_bar(series_id=catalog.minute_series.id))
    db_session.add(_bar(series_id=catalog.daily_series.id))
    db_session.commit()


def test_duplicate_series_identity_is_rejected(db_session: Session) -> None:
    catalog = seed_synthetic_catalog(db_session)

    with pytest.raises(IntegrityError):
        CatalogCommands.register_data_series(
            db_session,
            contract_id=catalog.contract.id,
            calendar_id=catalog.calendar.id,
            interval="1m",
            price_scale=2,
            quantity_scale=0,
        )
    db_session.rollback()


def test_invalid_ohlcv_relationship_is_rejected(db_session: Session) -> None:
    catalog = seed_synthetic_catalog(db_session)
    invalid = _bar(series_id=catalog.minute_series.id)
    invalid.high_price = Decimal("99.00")
    db_session.add(invalid)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_canonical_bar_preserves_catalog_maximum_decimal_scale(db_session: Session) -> None:
    catalog = seed_synthetic_catalog(db_session)
    catalog.minute_series.price_scale = 12
    catalog.minute_series.quantity_scale = 12
    db_session.flush()
    expected = Decimal("100.123456789012")
    bar = _bar(series_id=catalog.minute_series.id)
    bar.open_price = expected
    bar.high_price = Decimal("101.123456789012")
    bar.low_price = Decimal("99.123456789012")
    bar.close_price = expected
    bar.volume = Decimal("42.123456789012")
    db_session.add(bar)
    db_session.commit()
    db_session.expire_all()

    stored = db_session.scalar(select(CanonicalBar).where(CanonicalBar.id == bar.id))

    assert stored is not None
    assert stored.open_price == expected
    assert stored.close_price == expected
    assert stored.volume == Decimal("42.123456789012")
