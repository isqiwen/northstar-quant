"""A fixed multi-session publication retains gaps, declared days and per-session evidence."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.apps.data_hub import api_pb2
from northstar_quant.apps.data_hub.application import create_app
from northstar_quant.data_management.catalog.models import (
    DatasetSnapshotImportQualityPin,
    DatasetSnapshotPartition,
)
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.publication_client import PublicationClient
from northstar_quant.data_management.publications import PublishedDatasets
from northstar_quant.data_management.quality.evaluations import MinuteQualityEvaluationCommand
from northstar_quant.data_management.quality.minute_service import MinuteQualityEvaluationService
from northstar_quant.data_management.research_reader import load_dataset
from northstar_quant.data_management.snapshots.publication import (
    PublishDatasetSnapshotCommand,
    SnapshotImportQualityPinSelection,
    SnapshotPartitionSelection,
)
from northstar_quant.data_management.snapshots.service import DatasetSnapshotPublicationService
from northstar_quant.web.protobuf import decode
from tests.data_management.test_research import _csv, _receive, _spec


def _publish(engine, datasets):
    """Publish through the canonical owner, rechecking session quality at the common cutoff."""
    ids = [item.snapshot_id for item in datasets]
    with Session(engine) as session:
        partitions = session.scalars(
            select(DatasetSnapshotPartition).where(DatasetSnapshotPartition.manifest_id.in_(ids))
        ).all()
        pins = tuple(
            SnapshotImportQualityPinSelection(pin.import_run_id, pin.import_quality_evaluation_id)
            for pin in session.scalars(
                select(DatasetSnapshotImportQualityPin).where(
                    DatasetSnapshotImportQualityPin.manifest_id.in_(ids)
                )
            )
        )
    cutoff = max(bar.available_at for item in datasets for bar in item.bars)
    selections = []
    for part in partitions:
        with Session(engine) as session:
            quality = MinuteQualityEvaluationService(session).evaluate(
                MinuteQualityEvaluationCommand(
                    series_id=part.series_id,
                    from_trading_day=part.trading_day_from,
                    to_trading_day=part.trading_day_to,
                    as_of=cutoff,
                    idempotency_key=str(uuid4()),
                    correlation_id=str(uuid4()),
                )
            )
        selections.append(
            SnapshotPartitionSelection(
                part.series_id,
                part.trading_day_from,
                part.trading_day_to,
                quality.quality_evaluation_id,
            )
        )
    with Session(engine) as session:
        return (
            DatasetSnapshotPublicationService(session)
            .publish(
                PublishDatasetSnapshotCommand(
                    available_at_cutoff=max(
                        bar.available_at for item in datasets for bar in item.bars
                    ),
                    partitions=tuple(selections),
                    import_quality_pins=pins,
                    idempotency_key=str(uuid4()),
                    correlation_id=str(uuid4()),
                )
            )
            .snapshot_id
        )


def _shift(library, path, *, offset, trading_day=None, kind="DAY", symbol="RB2605"):
    spec = _spec()
    original = _csv(path).read_text()
    for minute in reversed(range(4)):
        before = spec.session_open + timedelta(minutes=minute)
        after = before + offset
        original = original.replace(
            before.isoformat().replace("+00:00", "Z"), after.isoformat().replace("+00:00", "Z")
        )
    path.write_text(original)
    return _receive(
        library,
        path,
        replace(
            spec,
            session_open=spec.session_open + offset,
            session_close=spec.session_close + offset,
            trading_day=trading_day or (spec.trading_day + offset),
            session_kind=kind,
            symbol=symbol,
        ),
    )


def test_night_day_and_next_day_keep_fixed_sessions_and_offline_parquet(
    postgres_engine,
    clean_database,
    tmp_path,
):
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    day = _receive(library, _csv(tmp_path / "day.csv"), _spec())
    night = _shift(
        library,
        tmp_path / "night.csv",
        offset=timedelta(hours=-12),
        trading_day=_spec().trading_day,
        kind="NIGHT",
    )
    following = _shift(library, tmp_path / "following.csv", offset=timedelta(days=1))
    identifier = _publish(postgres_engine, [following, day, night])
    fixed = load_dataset(postgres_engine, identifier)
    with pytest.raises(LookupError):
        library.load_dataset(identifier)
    assert identifier not in {item.snapshot_id for item in library.list_datasets()}
    library.publications.publish(fixed)
    assert library.load_dataset(identifier) == fixed
    assert fixed.bars == night.bars + day.bars + following.bars
    assert fixed.market == day.market
    assert fixed.details is not None
    assert fixed.details.summary.trading_days == (
        _spec().trading_day,
        _spec().trading_day + timedelta(days=1),
    )
    assert [spec.session_kind for spec in fixed.details.import_specs] == ["NIGHT", "DAY", "DAY"]
    assert len(fixed.details.sources) == len(fixed.details.minute_quality) == 3
    assert sum(pin.observed_count for pin in fixed.details.minute_quality) == 9
    assert identifier in {item.snapshot_id for item in library.list_datasets()}
    library.publications.publish(fixed)
    assert PublishedDatasets(library.publications.root).load_dataset(identifier) == fixed
    with TestClient(create_app(postgres_engine, library), base_url="http://core.local") as api:
        response = api.get(f"/api/datasets/{identifier}")
        assert response.status_code == 200
        details = decode(api_pb2.DatasetDetails.DESCRIPTOR, response.content)
        assert details["trading_days"] == ["2026-01-07", "2026-01-08"]
        assert len(details["import_specs"]) == 3

        def remote(request):
            result = api.get(request.url.raw_path.decode())
            return httpx2.Response(result.status_code, content=result.content)

        reader = PublicationClient(
            "http://core.local",
            library.publications.root,
            "market-published",
            transport=httpx2.MockTransport(remote),
        )
        assert reader.load_dataset(identifier) == fixed

        def offline(request):
            raise httpx2.ConnectError("owner offline")

        reader.transport = httpx2.MockTransport(offline)
        assert reader.load_dataset(identifier) == fixed
    # No synthetic bars inserted for the overnight/daytime gaps; original snapshots stay unchanged.
    assert library.load_dataset(day.snapshot_id) == day
    for source in fixed.details.sources:
        assert source.source_id in {
            item.source_id for data in (night, day, following) for item in data.details.sources
        }


@pytest.mark.parametrize(
    "offset,symbol,message",
    [
        (timedelta(minutes=1), "RB2605", "overlapping sessions"),
        (timedelta(days=1), "RB2606", "contract identity"),
    ],
)
def test_fixed_partitions_cannot_disguise_overlap_or_another_contract(
    postgres_engine,
    clean_database,
    tmp_path,
    offset,
    symbol,
    message,
):
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    first = _receive(library, _csv(tmp_path / "first.csv"), _spec())
    other = _shift(library, tmp_path / "other.csv", offset=offset, symbol=symbol)
    identifier = _publish(postgres_engine, [first, other])
    with pytest.raises(ValueError, match=message):
        library.load_dataset(identifier)
    assert identifier not in {item.snapshot_id for item in library.list_datasets()}


def test_a_later_session_cannot_move_the_declared_trading_day_backwards(
    postgres_engine,
    clean_database,
    tmp_path,
):
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    night = _shift(
        library,
        tmp_path / "night.csv",
        offset=timedelta(hours=12),
        trading_day=_spec().trading_day + timedelta(days=2),
        kind="NIGHT",
    )
    day = _shift(library, tmp_path / "day.csv", offset=timedelta(days=1))
    identifier = _publish(postgres_engine, [night, day])
    with pytest.raises(ValueError, match="decreasing trading days"):
        library.load_dataset(identifier)
