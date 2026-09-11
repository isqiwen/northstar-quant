"""Public CSV-to-immutable-data behavior on the real PostgreSQL baseline."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from northstar_quant.data_management.catalog.models import (
    CanonicalBar,
    DatasetSnapshotManifest,
    SourceReceipt,
)
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.research import ResearchDataset
from northstar_quant.data_management.research_input import ImportSpec


def _receive(library: DataLibrary, path: Path, spec: ImportSpec) -> ResearchDataset:
    attempt = library.receive(
        path.read_bytes(),
        filename=path.name,
        source_name=spec.source_name,
        use_basis="Locally generated integration data; retention and private research permitted.",
        allow_retention=True,
        allow_download=True,
        spec=spec.to_mapping(),
        request_id=str(uuid4()),
    )
    if attempt["status"] != "PUBLISHED":
        raise ValueError(attempt["error"])
    return library.load_dataset(UUID(str(attempt["snapshot_id"])))


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
            "session_kind": "DAY",
            "session_open": "2026-01-07T01:00:00Z",
            "session_close": "2026-01-07T01:03:00Z",
            "source_name": "SYNTHETIC",
            "source_reference": "Locally generated integration input",
            "availability_basis": "SYNTHETIC",
            "availability_note": "Engineering clock: each generated bar arrives at completion",
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    monkeypatch.setattr("northstar_quant.data_management.library.code_revision", lambda: "a" * 40)
    monkeypatch.setattr(
        "northstar_quant.data_management.processing.code_revision", lambda: "a" * 40
    )
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    path = _csv(tmp_path / "bars.csv")
    first = _receive(library, path, _spec())
    repeated = _receive(library, path, _spec())
    assert first == repeated == library.load_dataset(first.snapshot_id)
    assert [bar.close for bar in first.bars] == [Decimal(100), Decimal(101), Decimal(102)]
    assert all(bar.completed_at == bar.event_time + timedelta(minutes=1) for bar in first.bars)
    second = _receive(library, path, replace(_spec(), symbol="RB2606"))
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
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    path = _csv(tmp_path / "bars.csv", rows=2)
    with pytest.raises(ValueError, match="missing bars"):
        _receive(library, path, _spec())
    assert library.list_datasets() == ()
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetSnapshotManifest)) == 0
        assert session.scalar(select(func.count()).select_from(CanonicalBar)) == 0
    _csv(path)
    path.write_text(path.read_text() + path.read_text().splitlines(keepends=True)[1])
    with pytest.raises(ValueError, match="repeated event times"):
        _receive(library, path, _spec())
    assert library.list_datasets() == ()
    repaired = _receive(library, _csv(path), _spec())
    with pytest.raises(ValueError, match="missing bars"):
        _receive(library, _csv(path, rows=2), _spec())
    # Different source bytes can reference the same accepted observations.
    # Their import-quality evidence must remain reusable across research runs.
    _csv(path)
    path.write_text(path.read_text() + "\n")
    another_receipt = _receive(library, path, _spec())
    assert another_receipt.bars == repaired.bars
    assert another_receipt.market == repaired.market


def test_non_tick_price_cannot_become_canonical_data(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    path = _csv(tmp_path / "off-tick.csv", change="100,103,99,100.5,10")
    with pytest.raises(ValueError, match="data import"):
        _receive(library, path, _spec())
    assert library.list_datasets() == ()
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(CanonicalBar)) == 0


def test_pinned_data_detects_storage_drift(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    dataset = _receive(library, _csv(tmp_path / "bars.csv"), _spec())
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
        library.load_dataset(dataset.snapshot_id)


@pytest.mark.parametrize("availability_basis", ["SYNTHETIC", "SOURCE_DECLARED", "FINAL_REVISED"])
def test_dataset_can_be_reopened_without_a_research_run_or_original_file(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    availability_basis: str,
) -> None:
    del clean_database
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    path = _csv(tmp_path / "accepted.csv")
    spec = replace(
        _spec(),
        source_name="synthetic_operator_file",
        availability_basis=availability_basis,
    )
    path.write_text(path.read_text().replace(",2026-01-07T01:01:00Z,", ",2026-01-07T01:01:02Z,", 1))
    if availability_basis == "FINAL_REVISED":
        with pytest.raises(ValueError, match="CSV row 2 FINAL_REVISED available_at"):
            _receive(library, path, spec)
        assert library.list_datasets() == ()
        with Session(postgres_engine) as session:
            assert session.scalar(select(func.count()).select_from(CanonicalBar)) == 0
        _csv(path)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    dataset = _receive(library, path, spec)
    expected_delay = timedelta(seconds=0 if availability_basis == "FINAL_REVISED" else 2)
    assert dataset.bars[0].available_at == dataset.bars[0].completed_at + expected_delay
    # Removing the external source must not remove the durable research input.
    path.unlink()
    listed = library.list_datasets()
    assert len(listed) == 1
    assert listed[0].snapshot_id == dataset.snapshot_id
    assert listed[0].bar_count == 3
    assert listed[0].session_open == spec.session_open
    assert listed[0].session_close == spec.session_close
    details = library.describe_dataset(dataset.snapshot_id)
    assert details == library.load_dataset(dataset.snapshot_id).details == dataset.details
    assert details.import_specs[0] == spec
    assert details.sources[0].source_name == "SYNTHETIC_OPERATOR_FILE"
    assert details.sources[0].content_hash == original_hash
    assert details.sources[0].retention_policy == "CONTROLLED"
    assert details.sources[0].acquisition_use == (
        "SYNTHETIC_TEST_ONLY" if availability_basis == "SYNTHETIC" else "PRIVATE_RESEARCH_ONLY"
    )
    assert details.minute_quality[0].expected_observation_count == 3
    assert details.minute_quality[0].observed_count == 3
    assert details.minute_quality[0].missing_observation_count == 0
    assert details.import_quality[0].rows_inserted == 3
    serialized = json.loads(json.dumps(details.to_dict()))
    assert serialized["availability_basis"] == availability_basis
    assert serialized["semantics"]["timestamp_convention"] == "BAR_START"
    assert serialized["semantics"]["volume_unit"] == "LOT"
    assert serialized["semantics"]["quantity_unit"] == "TON"
    with Session(postgres_engine) as session:
        assert session.scalar(text("SELECT count(*) FROM research_runs")) == 0


def test_reuploaded_observations_keep_the_original_pinned_source_evidence(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    path = _csv(tmp_path / "source.csv")
    first = _receive(library, path, _spec())
    original = library.describe_dataset(first.snapshot_id)
    path.write_text(path.read_text() + "\n")
    new_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    # Source declarations must not relabel already accepted canonical observations.
    another = _receive(
        library,
        path,
        replace(
            _spec(),
            source_name="OTHER_SOURCE",
            source_reference="Another operator file",
            availability_basis="FINAL_REVISED",
            availability_note="Retrospective simulated arrival times",
        ),
    )
    reused = library.describe_dataset(another.snapshot_id)
    assert reused.sources == original.sources
    assert reused.import_specs[0] == original.import_specs[0]
    assert reused.sources[0].content_hash != new_hash
    assert another.bars == first.bars
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(SourceReceipt.content_hash).where(
                    SourceReceipt.source_name == "OTHER_SOURCE"
                )
            )
            == new_hash
        )


@pytest.mark.parametrize("tampered_field", ["mapping", "receipt"])
def test_source_evidence_drift_blocks_both_opening_and_research_loading(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    tampered_field: str,
) -> None:
    del clean_database
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    dataset = _receive(library, _csv(tmp_path / "source.csv"), _spec())
    assert dataset.details is not None
    source = dataset.details.sources[0]
    with postgres_engine.begin() as connection:
        if tampered_field == "mapping":
            connection.execute(
                text(
                    "UPDATE import_run SET mapping = jsonb_set(mapping::jsonb, "
                    "'{session,availability_basis}', '\"SOURCE_DECLARED\"')::json WHERE id = :id"
                ),
                {"id": source.import_run_id},
            )
        else:
            connection.execute(
                text(
                    "UPDATE source_receipt SET acquisition_use = 'PRIVATE_RESEARCH_ONLY' "
                    "WHERE id = :id"
                ),
                {"id": source.receipt_id},
            )
    with pytest.raises(ValueError, match="source"):
        library.describe_dataset(dataset.snapshot_id)
    with pytest.raises(ValueError, match="source"):
        library.load_dataset(dataset.snapshot_id)


@pytest.mark.parametrize(
    ("opened", "trading_day"),
    [("2026-01-06T15:59:00Z", "2026-01-07"), ("2026-01-09T15:59:00Z", "2026-01-12")],
)
def test_night_publication_preserves_declared_day_across_midnight_and_weekend(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    opened: str,
    trading_day: str,
) -> None:
    from datetime import datetime

    from northstar_quant.data_management.publications import PublishedDatasets
    from northstar_quant.research.backtesting.session import TradingSession
    from northstar_quant.research.configuration import ResearchConfig

    del clean_database
    monkeypatch.setattr("northstar_quant.data_management.library.code_revision", lambda: "a" * 40)
    monkeypatch.setattr(
        "northstar_quant.data_management.processing.code_revision", lambda: "a" * 40
    )
    start = datetime.fromisoformat(opened)
    raw = _spec().to_mapping() | {
        "session_kind": "NIGHT",
        "trading_day": trading_day,
        "session_open": opened,
        "session_close": (start + timedelta(minutes=3)).isoformat().replace("+00:00", "Z"),
    }
    spec = ImportSpec.from_mapping(raw)
    path = tmp_path / "night.csv"
    lines = ["event_time,available_at,source_record_id,open,high,low,close,volume"]
    for index in range(3):
        event = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        available = (
            (start + timedelta(minutes=index + 1, seconds=2)).isoformat().replace("+00:00", "Z")
        )
        lines.append(f"{event},{available},night-{index},100,100,100,100,10")
    path.write_text("\n".join(lines) + "\n")
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    dataset = _receive(library, path, spec)
    # Research's Parquet reader has no Data Hub database or source-file access.
    reopened = PublishedDatasets(library.publications.root).load_dataset(dataset.snapshot_id)
    assert dataset == reopened
    assert reopened.details is not None
    assert reopened.details.import_specs[0] == spec
    assert {bar.trading_day.isoformat() for bar in reopened.bars} == {trading_day}
    assert all(bar.available_at == bar.completed_at + timedelta(seconds=2) for bar in reopened.bars)
    checkpoints = []
    for fixed in (dataset, reopened):
        runtime = TradingSession(
            fixed.market,
            ResearchConfig(),
            snapshot_id=fixed.snapshot_id,
            content_hash=fixed.content_hash,
            data_details=fixed.details,
            interval_seconds=fixed.interval_seconds,
        )
        for bar in fixed.bars:
            runtime.advance(bar)
        checkpoints.append(runtime.checkpoint())
    assert checkpoints[0] == checkpoints[1]
    assert checkpoints[0]["trading_day"] == trading_day
    with pytest.raises(ValueError, match="DAY session"):
        ImportSpec.from_mapping(raw | {"session_kind": "DAY"})
    with pytest.raises(ValueError, match="NIGHT session"):
        ImportSpec.from_mapping(raw | {"trading_day": "2026-01-06"})
