"""End-to-end evidence for transient strict-Parquet ingestion.

These tests intentionally use only synthetic data.  They prove that the
Parquet adapter is an input decoder for the canonical-write
path, rather than a second persisted data format.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    CanonicalBar,
    ImportRecord,
    ImportRun,
    JobRun,
    SourceReceipt,
)
from northstar_quant.data.ingestion.imports import (
    PARQUET_MAPPING_VERSION,
    PARQUET_PROFILE_NAME,
    PARQUET_PROFILE_VERSION,
    OhlcvImportCommand,
)
from northstar_quant.data.ingestion.parquet_adapter import CanonicalOhlcvParquetAdapter
from northstar_quant.data.ingestion.service import OhlcvImportService

from .catalog_support import SyntheticCatalog, at_local, seed_synthetic_catalog

_SOURCE_TIMEZONE = ZoneInfo("Asia/Shanghai")
_PROFILE_METADATA = {
    b"northstar_quant.data.profile": PARQUET_PROFILE_NAME.encode("ascii"),
    b"northstar_quant.data.profile_version": PARQUET_PROFILE_VERSION.encode("ascii"),
}


def _command(
    path: Path,
    catalog: SyntheticCatalog,
    *,
    source_timezone_name: str = "Asia/Shanghai",
    idempotency_key: str = "parquet-import-001",
) -> OhlcvImportCommand:
    return OhlcvImportCommand(
        file_path=path,
        series_id=catalog.minute_series.id,
        source_name="SYNTHETIC-PARQUET",
        source_timezone_name=source_timezone_name,
        idempotency_key=idempotency_key,
        correlation_id="parquet-integration-correlation-001",
        causation_id="parquet-integration-causation-001",
    )


def _service(session: Session) -> OhlcvImportService:
    return OhlcvImportService(
        session,
        adapter=CanonicalOhlcvParquetAdapter(
            max_bytes=1_000_000,
            max_rows=100,
            max_field_bytes=256,
            max_uncompressed_bytes=1_000_000,
        ),
    )


def _write_valid_parquet(path: Path) -> None:
    """Write the exact versioned profile for the synthetic 1m catalog series."""

    schema = pa.schema(
        [
            pa.field("symbol", pa.string()),
            pa.field("interval", pa.string()),
            pa.field("event_time", pa.timestamp("us", tz="Asia/Shanghai")),
            pa.field("trading_day", pa.date32()),
            pa.field("available_at", pa.timestamp("us", tz="Asia/Shanghai")),
            pa.field("source_record_id", pa.string()),
            pa.field("price_currency", pa.string()),
            pa.field("volume_unit", pa.string()),
            pa.field("open_interest_unit", pa.string()),
            pa.field("turnover_currency", pa.string()),
            pa.field("turnover_multiplier", pa.decimal128(32, 0)),
            pa.field("open_price", pa.decimal128(24, 2)),
            pa.field("high_price", pa.decimal128(24, 2)),
            pa.field("low_price", pa.decimal128(24, 2)),
            pa.field("close_price", pa.decimal128(24, 2)),
            pa.field("volume", pa.decimal128(28, 0)),
            pa.field("turnover", pa.decimal128(32, 2)),
            pa.field("open_interest", pa.decimal128(28, 0)),
        ],
        metadata=_PROFILE_METADATA,
    )
    values: dict[str, list[object]] = {
        "symbol": ["RB2605", "RB2605"],
        "interval": ["1m", "1m"],
        "event_time": [
            datetime(2026, 1, 6, 21, 0, tzinfo=_SOURCE_TIMEZONE),
            datetime(2026, 1, 6, 21, 1, tzinfo=_SOURCE_TIMEZONE),
        ],
        "trading_day": [date(2026, 1, 7), date(2026, 1, 7)],
        "available_at": [
            datetime(2026, 1, 6, 21, 1, tzinfo=_SOURCE_TIMEZONE),
            datetime(2026, 1, 6, 21, 2, tzinfo=_SOURCE_TIMEZONE),
        ],
        "source_record_id": ["parquet-integration-0001", "parquet-integration-0002"],
        "price_currency": ["CNY", "CNY"],
        "volume_unit": ["LOT", "LOT"],
        "open_interest_unit": ["LOT", "LOT"],
        "turnover_currency": ["CNY", "CNY"],
        "turnover_multiplier": [Decimal("1"), Decimal("1")],
        "open_price": [Decimal("3500.00"), Decimal("3500.00")],
        "high_price": [Decimal("3501.00"), Decimal("3502.00")],
        "low_price": [Decimal("3499.00"), Decimal("3500.00")],
        "close_price": [Decimal("3500.00"), Decimal("3501.00")],
        "volume": [Decimal("10"), Decimal("15")],
        "turnover": [Decimal("35008.00"), Decimal("52510.50")],
        "open_interest": [Decimal("120000"), Decimal("120010")],
    }
    arrays = [pa.array(values[field.name], type=field.type) for field in schema]
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), path)


def test_parquet_import_reuses_canonical_writer_and_is_content_idempotent(
    db_session: Session, tmp_path: Path
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    path = tmp_path / "synthetic-ohlcv.parquet"
    _write_valid_parquet(path)
    command = _command(path, catalog)
    service = _service(db_session)

    first = service.import_file(command)
    replay = service.import_file(command)

    assert first.status == "SUCCEEDED"
    assert first.effect == "APPLIED"
    assert first.rows_read == 2
    assert first.rows_inserted == 2
    assert first.source_receipt_id is not None
    assert replay.import_run_id == first.import_run_id
    assert replay.effect == "NOOP"
    assert replay.replayed

    import_run = db_session.get(ImportRun, first.import_run_id)
    receipt = db_session.get(SourceReceipt, first.source_receipt_id)
    assert import_run is not None
    assert receipt is not None
    assert import_run.mapping_version == PARQUET_MAPPING_VERSION
    assert import_run.mapping is not None
    assert import_run.mapping["profile"] == {
        "name": PARQUET_PROFILE_NAME,
        "version": PARQUET_PROFILE_VERSION,
    }
    assert import_run.mapping["source_row_number_policy"] == (
        "parquet_physical_row_ordinal_plus_two_v1"
    )
    assert receipt.media_type == "application/vnd.apache.parquet"
    assert receipt.input_kind == "OPERATOR_FILE"
    assert receipt.retention_policy == "TRANSIENT"
    assert receipt.acquisition_use == "UNKNOWN"
    assert receipt.redistribution_policy == "UNKNOWN"
    assert receipt.source_timezone_name == "Asia/Shanghai"

    job = db_session.get(JobRun, first.job_run_id)
    bars = db_session.scalars(select(CanonicalBar).order_by(CanonicalBar.event_time)).all()
    records = db_session.scalars(
        select(ImportRecord)
        .where(ImportRecord.import_run_id == first.import_run_id)
        .order_by(ImportRecord.source_row_number)
    ).all()
    assert job is not None
    assert job.job_kind == "PARQUET_IMPORT_V1"
    assert len(bars) == 2
    assert [bar.event_time for bar in bars] == [
        at_local(2026, 1, 6, 21),
        at_local(2026, 1, 6, 21, 1),
    ]
    assert [bar.open_price for bar in bars] == [
        Decimal("3500.000000000000"),
        Decimal("3500.000000000000"),
    ]
    assert all(bar.import_run_id == first.import_run_id for bar in bars)
    assert all(bar.source_content_hash == receipt.content_hash for bar in bars)
    assert [(record.source_row_number, record.disposition) for record in records] == [
        (2, "INSERTED"),
        (3, "INSERTED"),
    ]
    assert db_session.scalar(select(func.count()).select_from(ImportRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(CanonicalBar)) == 2


def test_parquet_timezone_declaration_mismatch_is_a_durable_failed_import(
    db_session: Session, tmp_path: Path
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    path = tmp_path / "timezone-mismatch.parquet"
    _write_valid_parquet(path)

    result = _service(db_session).import_file(
        _command(
            path, catalog, source_timezone_name="UTC", idempotency_key="parquet-import-utc-001"
        )
    )

    assert result.status == "FAILED"
    assert result.effect == "REJECTED"
    assert result.rows_inserted == 0
    assert result.source_receipt_id is not None
    import_run = db_session.get(ImportRun, result.import_run_id)
    receipt = db_session.get(SourceReceipt, result.source_receipt_id)
    assert import_run is not None
    assert receipt is not None
    assert import_run.error_code == "SOURCE_TIMEZONE_DECLARATION_MISMATCH"
    assert import_run.mapping_version == PARQUET_MAPPING_VERSION
    assert receipt.source_timezone_name == "UTC"
    assert receipt.media_type == "application/vnd.apache.parquet"
    assert db_session.scalar(select(func.count()).select_from(CanonicalBar)) == 0
