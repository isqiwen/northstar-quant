"""A fixed multi-session publication retains gaps, declared days and per-session evidence."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
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
from tests.apps.browser import login_response
from tests.data_management.test_research import _csv, _receive, _spec


def _publish(engine, datasets, *, settlements=(), terms=()):
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
                    settlements=settlements,
                    terms=terms,
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
        assert login_response(api).status_code == 200
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


def test_cross_day_settlement_is_fixed_offline_replayable_and_survives_every_restart(
    postgres_engine,
    clean_database,
    tmp_path,
):
    from decimal import Decimal

    from sqlalchemy import create_engine, event

    from northstar_quant.accounting.settlement import SettlementFact
    from northstar_quant.accounting.terms import ChargeRate, FuturesTerms
    from northstar_quant.research.backtesting import run_research
    from northstar_quant.research.configuration import ResearchConfig
    from northstar_quant.research.configurations import ConfigurationStore
    from northstar_quant.research.paper import PaperStore
    from northstar_quant.research.storage import initialize
    from northstar_quant.strategies.configuration import StrategyConfig

    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    first = _receive(library, _csv(tmp_path / "first.csv"), _spec())
    following = _shift(library, tmp_path / "next.csv", offset=timedelta(days=1))
    fact = SettlementFact(
        "synthetic-settlement-20260107",
        first.market.contract_id,
        first.bars[0].trading_day,
        following.bars[0].trading_day,
        first.bars[-1].completed_at + timedelta(hours=6),
        first.bars[-1].available_at + timedelta(hours=6, minutes=1),
        Decimal(105),
        "SYNTHETIC test fixture; not historical exchange terms",
    )
    first_terms = FuturesTerms(
        "synthetic-day-one",
        first.market.contract_id,
        first.bars[0].event_time,
        following.bars[0].event_time,
        first.bars[0].event_time,
        "synthetic terms, not historical exchange evidence",
        ChargeRate(Decimal(0), Decimal(2)),
        ChargeRate(Decimal(0), Decimal(4)),
        ChargeRate(Decimal(0), Decimal(9)),
        ChargeRate(Decimal("0.1"), Decimal(0)),
        ChargeRate(Decimal("0.12"), Decimal(0)),
        Decimal(50),
        Decimal(200),
        Decimal("0.01"),
        "ROUND_HALF_UP",
    )
    next_terms = replace(
        first_terms,
        terms_id="synthetic-day-two",
        effective_from=following.bars[0].event_time,
        effective_until=following.bars[-1].available_at + timedelta(hours=1),
        open_fee=ChargeRate(Decimal(0), Decimal(3)),
    )
    identifier = _publish(
        postgres_engine, [first, following], settlements=(fact,), terms=(first_terms, next_terms)
    )
    fixed = load_dataset(postgres_engine, identifier)
    library.publications.publish(fixed)
    offline = PublishedDatasets(library.publications.root)
    assert offline.load_dataset(identifier) == fixed
    assert fixed.details.settlements == (fact,)
    assert fixed.details.terms == (first_terms, next_terms)
    with TestClient(create_app(postgres_engine, library), base_url="http://core.local") as api:
        assert login_response(api).status_code == 200
        response = api.get(f"/api/datasets/{identifier}")
        assert response.status_code == 200
        presented = decode(api_pb2.DatasetDetails.DESCRIPTOR, response.content)
        assert presented["terms"] == [first_terms.to_dict(), next_terms.to_dict()]
        assert presented["settlements"] == [fact.to_dict()]
    revised = load_dataset(
        postgres_engine,
        _publish(
            postgres_engine,
            [first, following],
            settlements=(fact,),
            terms=(first_terms, replace(next_terms, open_fee=ChargeRate(Decimal(0), Decimal(7)))),
        ),
    )
    assert revised.content_hash != fixed.content_hash
    assert offline.load_dataset(identifier) == fixed
    config = ResearchConfig(strategy=StrategyConfig.create(supplied={"threshold": "0.001"}))
    uncovered = load_dataset(
        postgres_engine,
        _publish(postgres_engine, [first, following], settlements=(fact,), terms=(first_terms,)),
    )
    with pytest.raises(ValueError, match="available and effective"):
        run_research(uncovered, config)
    batch = run_research(fixed, config).to_dict()
    assert batch == run_research(offline.load_dataset(identifier), config).to_dict()
    assert batch == run_research(replace(fixed, bars=tuple(reversed(fixed.bars))), config).to_dict()
    assert len(batch["settlements"]) == 1
    assert batch["settlements"][0]["price"] == "105"
    assert any(item["offset"] == "CLOSE_YESTERDAY" for item in batch["fills"])
    from zoneinfo import ZoneInfo

    from northstar_quant.research.evaluation import EvaluationPlan

    shanghai = ZoneInfo("Asia/Shanghai")
    local_summary = replace(
        fixed.details.summary,
        session_open=fixed.details.summary.session_open.astimezone(shanghai),
        session_close=fixed.details.summary.session_close.astimezone(shanghai),
    )
    local_details = replace(fixed.details, summary=local_summary)
    assert (
        EvaluationPlan.bind(fixed.snapshot_id, fixed.content_hash, local_details).to_dict()
        == batch["evaluation"]["plan"]
    )
    with pytest.raises(ValueError, match="explicit timezones"):
        EvaluationPlan.bind(
            fixed.snapshot_id,
            fixed.content_hash,
            replace(
                local_details,
                summary=replace(
                    local_summary, session_open=local_summary.session_open.replace(tzinfo=None)
                ),
            ),
        )
    evaluation = batch["evaluation"]
    assert evaluation["status"] == "COMPLETE_WINDOW"
    assert evaluation["plan"]["expected_bars"] == len(fixed.bars)
    assert evaluation["plan"]["sample_use"] == "EXPLORATORY_NOT_OUT_OF_SAMPLE"
    assert evaluation["annualized_return"] is None and evaluation["sharpe"] is None
    assert evaluation["excess_return"] == batch["summary"]["total_return"]
    with pytest.raises(ValueError, match="fixed evaluation window count"):
        run_research(replace(fixed, bars=fixed.bars[:-1]), config)
    for point in batch["equity_curve"]:
        assert Decimal(point["trade_realized_pnl"]) + Decimal(point["settlement_pnl"]) == Decimal(
            point["realized_pnl"]
        )
        assert Decimal(point["gross_exposure"]) >= abs(Decimal(point["net_exposure"]))
        assert Decimal(point["available_after_reservations"]) == (
            Decimal(point["equity"])
            - Decimal(point["margin_used"])
            - Decimal(point["reserved_fee"])
            - Decimal(point["reserved_margin"])
            - Decimal(point["reserved_loss"])
        )
    assert all("margin_used" in point and "available" in point for point in batch["equity_curve"])
    assert {point["terms_id"] for point in batch["equity_curve"]} == {
        first_terms.terms_id,
        next_terms.terms_id,
    }
    for fill in batch["fills"]:
        if fill["offset"] == "CLOSE_YESTERDAY":
            assert Decimal(fill["fee"]) == Decimal(9) * fill["quantity_lots"]
    from northstar_quant.research.backtesting.report import build_result
    from northstar_quant.research.backtesting.session import TradingSession, TradingStep

    replay = TradingSession(
        fixed.market,
        config,
        snapshot_id=fixed.snapshot_id,
        content_hash=fixed.content_hash,
        data_details=fixed.details,
    )
    try:
        steps = [step for bar in fixed.bars if (step := replay.advance(bar)) is not None]
        altered = list(steps)
        index = next(i for i, step in enumerate(steps) if step.settlements)
        document = steps[index].to_dict()
        document["settlements"] = []
        altered[index] = TradingStep.from_dict(document)
        with pytest.raises(ValueError, match="complete committed"):
            build_result(replay, altered)
        assert build_result(replay, steps).to_dict() == batch
    finally:
        replay.close()
    # The actual Research state owner is local SQLite, independent of Data Hub/PostgreSQL.
    engine = create_engine(f"sqlite:///{tmp_path / 'research.db'}")
    initialize(engine)
    saved = ConfigurationStore(engine).save_configuration("cross-day engineering", config)
    session_id = uuid4()
    PaperStore(engine, offline).create(identifier, saved["configuration_id"], request_id=session_id)
    engine.dispose()
    for index, _ in enumerate(fixed.bars):
        engine = create_engine(f"sqlite:///{tmp_path / 'research.db'}")
        store = PaperStore(engine, offline)
        command = uuid4()
        if index == len(first.bars):
            before = store.get(session_id)

            def fail_commit(_conn, _cursor, statement, _parameters, _context, _many):
                if statement.startswith("UPDATE paper_sessions"):
                    raise RuntimeError("settlement checkpoint interrupted")

            event.listen(engine, "before_cursor_execute", fail_commit)
            try:
                with pytest.raises(RuntimeError, match="settlement checkpoint interrupted"):
                    store.advance(session_id, request_id=command)
            finally:
                event.remove(engine, "before_cursor_execute", fail_commit)
            assert store.get(session_id) == before
        result = store.advance(session_id, request_id=command)
        assert store.advance(session_id, request_id=command) == result
        engine.dispose()
    engine = create_engine(f"sqlite:///{tmp_path / 'research.db'}")
    try:
        result = PaperStore(engine, offline).get(session_id)
        for name in (
            "summary",
            "fills",
            "settlements",
            "equity_curve",
            "decisions",
            "pending_order",
        ):
            assert result[name] == batch[name]
    finally:
        engine.dispose()


def test_day_transition_without_available_fixed_settlement_rejects_without_changing_account(
    postgres_engine,
    clean_database,
    tmp_path,
):
    from northstar_quant.research.backtesting.session import TradingSession
    from northstar_quant.research.configuration import ResearchConfig

    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    first = _receive(library, _csv(tmp_path / "first.csv"), _spec())
    following = _shift(library, tmp_path / "next.csv", offset=timedelta(days=1))
    fixed = load_dataset(postgres_engine, _publish(postgres_engine, [first, following]))
    session = TradingSession(
        fixed.market,
        ResearchConfig(),
        snapshot_id=fixed.snapshot_id,
        content_hash=fixed.content_hash,
        data_details=fixed.details,
    )
    try:
        for bar in first.bars:
            session.advance(bar)
        before = session.checkpoint()
        with pytest.raises(ValueError, match="fixed, causal settlement"):
            session.advance(following.bars[0])
        assert session.checkpoint() == before
    finally:
        session.close()


def test_settlement_price_changes_publication_identity_and_invalid_scope_cannot_publish(
    postgres_engine,
    clean_database,
    tmp_path,
):
    from decimal import Decimal

    from northstar_quant.accounting.settlement import SettlementFact
    from northstar_quant.data_management.snapshots.publication import (
        DatasetSnapshotPublicationError,
    )

    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    first = _receive(library, _csv(tmp_path / "first.csv"), _spec())
    following = _shift(library, tmp_path / "next.csv", offset=timedelta(days=1))
    fact = SettlementFact(
        "synthetic-settlement",
        first.market.contract_id,
        first.bars[0].trading_day,
        following.bars[0].trading_day,
        first.bars[-1].completed_at + timedelta(hours=6),
        first.bars[-1].available_at + timedelta(hours=6, minutes=1),
        Decimal(105),
        "synthetic accounting input",
    )
    original = load_dataset(
        postgres_engine, _publish(postgres_engine, [first, following], settlements=(fact,))
    )
    revised = load_dataset(
        postgres_engine,
        _publish(
            postgres_engine, [first, following], settlements=(replace(fact, price=Decimal(106)),)
        ),
    )
    assert original.content_hash != revised.content_hash
    assert load_dataset(postgres_engine, original.snapshot_id) == original
    for invalid in (
        (replace(fact, contract_id=uuid4()),),
        (replace(fact, available_at=following.bars[-1].available_at + timedelta(seconds=1)),),
        (fact, replace(fact, settlement_id="conflicting-same-day")),
    ):
        with pytest.raises(DatasetSnapshotPublicationError, match="settlement"):
            _publish(postgres_engine, [first, following], settlements=invalid)

    from northstar_quant.data_management.catalog.models import DatasetSnapshotManifest
    from northstar_quant.data_management.snapshots.service import DatasetSnapshotResolutionError

    with Session(postgres_engine) as session, session.begin():
        # Inject corruption beyond the normal immutable-table guard, as in a bad restore.
        session.execute(text("SET LOCAL session_replication_role = replica"))
        manifest = session.get(DatasetSnapshotManifest, original.snapshot_id)
        assert manifest is not None
        manifest.settlements = [{**fact.to_dict(), "unhashed_override": "106"}]
    with pytest.raises(DatasetSnapshotResolutionError, match="settlement"):
        load_dataset(postgres_engine, original.snapshot_id)
