"""Public CSV-to-immutable-data behavior on the real PostgreSQL baseline."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import CanonicalBar, DatasetSnapshotManifest
from northstar_quant.data.research import ImportSpec, import_csv, load_dataset


def _spec() -> ImportSpec:
    return ImportSpec.from_mapping(
        {
            "exchange": "SHFE",
            "product": "RB",
            "symbol": "RB2605",
            "timezone": "Asia/Shanghai",
            "currency": "CNY",
            "quantity_unit": "TON",
            "price_tick": "1",
            "multiplier": "10",
            "trading_day": "2026-01-07",
            "session_open": "2026-01-07T01:00:00Z",
            "session_close": "2026-01-07T01:03:00Z",
            "source_name": "SYNTHETIC",
        }
    )


def _csv(path: Path, *, rows: int = 3, change: str | None = None) -> Path:
    contents = "event_time,available_at,source_record_id,open,high,low,close,volume\n"
    for minute in range(rows):
        contents += (
            f"2026-01-07T01:0{minute}:00Z,2026-01-07T01:0{minute + 1}:00Z,"
            f"r{minute},100,103,99,{100 + minute},10\n"
        )
    if change is not None:
        contents = contents.replace("100,103,99,100,10", change)
    path.write_text(contents)
    return path


def test_import_pins_complete_session_replays_and_accepts_another_contract(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    path = _csv(tmp_path / "bars.csv")
    first = import_csv(postgres_engine, path, _spec())
    repeated = import_csv(postgres_engine, path, _spec())
    assert first == repeated == load_dataset(postgres_engine, first.snapshot_id)
    assert [bar.close for bar in first.bars] == [Decimal(100), Decimal(101), Decimal(102)]
    assert all(bar.completed_at == bar.event_time + timedelta(minutes=1) for bar in first.bars)
    second = import_csv(postgres_engine, path, replace(_spec(), symbol="RB2606"))
    assert second.market.contract_id != first.market.contract_id
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetSnapshotManifest)) == 2
        assert session.scalar(select(func.count()).select_from(CanonicalBar)) == 6


def test_incomplete_csv_is_rejected_before_writes_and_can_be_repaired(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    path = _csv(tmp_path / "bars.csv", rows=2)
    with pytest.raises(ValueError, match="missing bars"):
        import_csv(postgres_engine, path, _spec())
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetSnapshotManifest)) == 0
        assert session.scalar(select(func.count()).select_from(CanonicalBar)) == 0
    repaired = import_csv(postgres_engine, _csv(path), _spec())
    with pytest.raises(ValueError, match="missing bars"):
        import_csv(postgres_engine, _csv(path, rows=2), _spec())
    # Different source bytes can reference the same accepted observations.
    # Their import-quality evidence must remain reusable across research runs.
    _csv(path)
    path.write_text(path.read_text() + "\n")
    another_receipt = import_csv(postgres_engine, path, _spec())
    assert another_receipt.bars == repaired.bars
    assert another_receipt.market == repaired.market


def test_non_tick_price_cannot_become_canonical_data(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    path = _csv(tmp_path / "off-tick.csv", change="100,103,99,100.5,10")
    with pytest.raises(ValueError, match="data import"):
        import_csv(postgres_engine, path, _spec())
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(CanonicalBar)) == 0


def test_pinned_data_detects_storage_drift(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    dataset = import_csv(postgres_engine, _csv(tmp_path / "bars.csv"), _spec())
    with pytest.raises(SQLAlchemyError):
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE canonical_bar SET close_price = 101 WHERE id = :id"),
                {"id": dataset.bars[0].observation_id},
            )
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text("UPDATE canonical_bar SET close_price = 101 WHERE id = :id"),
            {"id": dataset.bars[0].observation_id},
        )
    with pytest.raises(ValueError):
        load_dataset(postgres_engine, dataset.snapshot_id)
