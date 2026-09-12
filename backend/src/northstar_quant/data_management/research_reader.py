"""Resolve fixed research sessions and their original evidence in one read transaction."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from northstar_quant.accounting.settlement import SettlementFact
from northstar_quant.accounting.terms import FuturesTerms
from northstar_quant.market_data import Instrument, MarketBar

from .catalog.models import (
    DatasetSnapshotImportQualityPin,
    DatasetSnapshotManifest,
    DatasetSnapshotPartition,
    DatasetSnapshotSeriesQualityPin,
)
from .quality.import_service import current_import_quality_state
from .research import (
    DatasetDetails,
    DatasetImportQuality,
    DatasetMinuteQuality,
    DatasetSource,
    DatasetSummary,
    ResearchDataset,
    _CtpSegment,
    _digest,
    _ResearchCsv,
    _source_evidence,
)
from .research_input import ImportSpec
from .snapshots.service import DatasetSnapshotResolutionService, ResolvedDatasetSnapshot


def load_dataset(engine: Engine, snapshot_id: UUID) -> ResearchDataset:
    """Verify observations and their original source evidence in one read transaction."""

    with Session(
        engine.execution_options(northstar_write=True)
        if engine.dialect.name == "sqlite"
        else engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        dataset, _details = _read_dataset(session, snapshot_id)
        return dataset


def _summary(
    manifest: DatasetSnapshotManifest, partition: DatasetSnapshotPartition
) -> DatasetSummary:
    return DatasetSummary(
        snapshot_id=manifest.id,
        content_hash=manifest.content_hash,
        exchange=partition.exchange_code,
        product=partition.product_code,
        symbol=partition.contract_code,
        trading_days=(partition.trading_day_from,),
        session_open=partition.event_time_from,
        session_close=partition.event_time_to + timedelta(minutes=int(partition.interval[:-1])),
        bar_count=partition.row_count,
        published_at=manifest.created_at,
    )


def _read_dataset(session: Session, snapshot_id: UUID) -> tuple[ResearchDataset, DatasetDetails]:
    resolved = DatasetSnapshotResolutionService(session).resolve(snapshot_id)
    partitions = session.scalars(
        select(DatasetSnapshotPartition).where(DatasetSnapshotPartition.manifest_id == snapshot_id)
    ).all()
    pins = session.scalars(
        select(DatasetSnapshotImportQualityPin).where(
            DatasetSnapshotImportQualityPin.manifest_id == snapshot_id
        )
    ).all()
    if {pin.import_run_id for pin in pins} != {
        member.canonical_bar.import_run_id for member in resolved.members
    }:
        raise ValueError("snapshot source pins do not match the original observation imports")
    if not partitions:
        raise ValueError("research requires nonempty immutable snapshot partitions")
    parts = [_read_partition(session, resolved, partition) for partition in partitions]
    parts.sort(key=lambda item: item[1].summary.session_open)
    first, first_details = parts[0]
    for (previous, previous_details), (current, current_details) in zip(parts, parts[1:]):
        if (
            current.market != first.market
            or current.interval_seconds != first.interval_seconds
            or current_details.volume_unit != first_details.volume_unit
            or current_details.adjustment != first_details.adjustment
        ):
            raise ValueError("research snapshot mixes contract identity or economics")
        if previous_details.summary.session_close > current_details.summary.session_open:
            raise ValueError("research snapshot contains overlapping sessions")
        if previous.bars[-1].trading_day > current.bars[0].trading_day:
            raise ValueError("research snapshot contains decreasing trading days")
        if (
            current_details.import_specs[0].availability_basis
            != first_details.import_specs[0].availability_basis
        ):
            raise ValueError("research snapshot mixes availability assumptions")
        if current_details.processing_provenance != first_details.processing_provenance:
            raise ValueError("research snapshot mixes callback reconstruction provenance")
    bars = tuple(bar for dataset, _ in parts for bar in dataset.bars)
    if len({bar.observation_id for bar in bars}) != len(bars):
        raise ValueError("research snapshot repeats observations")
    if any(a.available_at > b.available_at for a, b in zip(bars, bars[1:])):
        raise ValueError("research snapshot requires monotonic availability across sessions")
    details = replace(
        first_details,
        settlements=tuple(SettlementFact.from_dict(item) for item in resolved.manifest.settlements),
        terms=tuple(FuturesTerms.from_dict(item) for item in resolved.manifest.terms),
        summary=replace(
            first_details.summary,
            trading_days=tuple(dict.fromkeys(bar.trading_day for bar in bars)),
            session_close=parts[-1][1].summary.session_close,
            bar_count=len(bars),
        ),
        import_specs=tuple(spec for _, item in parts for spec in item.import_specs),
        sources=tuple(dict.fromkeys(source for _, item in parts for source in item.sources)),
        import_quality=tuple(
            dict.fromkeys(pin for _, item in parts for pin in item.import_quality)
        ),
        minute_quality=tuple(pin for _, item in parts for pin in item.minute_quality),
    )
    return replace(first, bars=bars, details=details), details


def _read_partition(
    session: Session,
    resolved: ResolvedDatasetSnapshot,
    partition: DatasetSnapshotPartition,
) -> tuple[ResearchDataset, DatasetDetails]:
    snapshot_id = resolved.manifest.id
    if (
        partition.interval not in {"1m", "5m", "15m", "30m", "60m"}
        or partition.timestamp_convention != "BAR_START"
    ):
        raise ValueError("research requires fixed-interval BAR_START data")
    market = Instrument(
        contract_id=partition.contract_id,
        symbol=partition.contract_code,
        exchange_timezone=partition.exchange_timezone_name,
        currency=partition.price_currency,
        quantity_unit=partition.quantity_unit,
        price_tick=partition.price_tick,
        multiplier=partition.contract_multiplier,
    )
    bars: list[MarketBar] = []
    member_import_ids: set[UUID] = set()
    for member in resolved.members:
        if member.partition_id != partition.id:
            continue
        bar = member.canonical_bar
        if bar.revision_number != 1 or bar.supersedes_canonical_bar_id is not None:
            raise ValueError("research does not replay observation corrections")
        if bar.import_run_id is None:
            raise ValueError("research requires original import evidence for every observation")
        member_import_ids.add(bar.import_run_id)
        completed = member.event_time + timedelta(minutes=int(partition.interval[:-1]))
        if member.available_at < completed:
            raise ValueError("a bar cannot be available before its completion")
        bars.append(
            MarketBar(
                observation_id=bar.id,
                event_time=member.event_time,
                completed_at=completed,
                available_at=member.available_at,
                trading_day=bar.trading_day,
                close=bar.close_price,
                volume=bar.volume,
            )
        )
    if not bars or len({bar.trading_day for bar in bars}) != 1:
        raise ValueError("research requires nonempty observations from exactly one trading day")
    bars.sort(key=lambda bar: (bar.available_at, bar.event_time, str(bar.observation_id)))
    sources: list[DatasetSource] = []
    import_quality: list[DatasetImportQuality] = []
    specs: list[ImportSpec] = []
    stream_provenance: dict[str, object] | None = None
    pins = session.scalars(
        select(DatasetSnapshotImportQualityPin)
        .where(
            DatasetSnapshotImportQualityPin.manifest_id == snapshot_id,
            DatasetSnapshotImportQualityPin.import_run_id.in_(member_import_ids),
        )
        .order_by(DatasetSnapshotImportQualityPin.import_run_id)
    ).all()
    if {pin.import_run_id for pin in pins} != member_import_ids:
        raise ValueError("snapshot source pins do not match the original observation imports")
    for pin in pins:
        imported = pin.import_run
        receipt = imported.source_receipt
        mapping = imported.mapping
        if (
            receipt is None
            or mapping is None
            or _digest(mapping) != imported.mapping_hash
            or imported.mapping_version
            not in {
                _ResearchCsv.mapping_version,
                _CtpSegment.mapping_version,
            }
        ):
            raise ValueError("snapshot source mapping is missing, unsupported or has drifted")
        source_spec = mapping.get("session")
        if not isinstance(source_spec, dict):
            raise ValueError("snapshot original source session metadata is missing")
        spec = ImportSpec.from_mapping(source_spec)
        if imported.mapping_version == _CtpSegment.mapping_version:
            provenance = mapping.get("stream")
            if (
                spec.availability_basis != "LOCAL_CAPTURE_RECONSTRUCTED"
                or not isinstance(provenance, dict)
                or provenance.get("price_basis") != "OBSERVED_CTP_LAST_PRICE"
                or provenance.get("volume_basis") != "CUMULATIVE_DELTA_AT_SNAPSHOT_TIME"
                or provenance.get("time_basis") != "RECONSTRUCTED_FROM_LOCAL_RECEIPT_CLOCK"
            ):
                raise ValueError("CTP snapshot reconstruction meaning is missing or has drifted")
            if stream_provenance is not None and stream_provenance != provenance:
                raise ValueError("CTP snapshot mixes different fixed callback prefixes")
            stream_provenance = provenance
        elif spec.availability_basis == "LOCAL_CAPTURE_RECONSTRUCTED":
            raise ValueError("operator CSV cannot claim locally captured CTP evidence")
        if (
            spec.source_name.upper() != receipt.source_name
            or spec.timezone != receipt.source_timezone_name
        ):
            raise ValueError("snapshot original source identity differs from its session metadata")
        # Pins bind the complete scanner fingerprint, not just an evaluation ID.
        # Recompute before exposing mutable import/receipt metadata as evidence.
        current = current_import_quality_state(session, pin.import_run_id)
        if (
            current.input_fingerprint != pin.input_fingerprint
            or current.outcome != pin.outcome
            or current.delivery_gate != pin.delivery_gate
        ):
            raise ValueError("snapshot original source evidence no longer matches its quality pin")
        from .library import _sources

        archive = mapping.get("archive")
        if not isinstance(archive, dict):
            raise ValueError("snapshot original source archive identity is missing")
        source = (
            session.execute(
                select(_sources).where(_sources.c.source_id == UUID(str(archive.get("source_id")))),
            )
            .mappings()
            .one_or_none()
        )
        if (
            source is None
            or _digest(_source_evidence(dict(source))) != archive.get("evidence_hash")
            or source["evidence_hash"] != archive.get("evidence_hash")
            or source["content_hash"] != receipt.content_hash
            or source["byte_count"] != receipt.byte_count
            or str(source["source_name"]).upper() != receipt.source_name
            or source["allow_retention"] is not True
            or (source["input_kind"] == "CTP_CALLBACK_SEGMENT")
            != (imported.mapping_version == _CtpSegment.mapping_version)
        ):
            raise ValueError("snapshot original source archive evidence has drifted")
        specs.append(spec)
        sources.append(
            DatasetSource(
                import_run_id=imported.id,
                receipt_id=receipt.id,
                source_name=receipt.source_name,
                content_hash=receipt.content_hash,
                byte_count=receipt.byte_count,
                received_at=source["received_at"],
                acquisition_use=receipt.acquisition_use,
                redistribution_policy=receipt.redistribution_policy,
                retention_policy=receipt.retention_policy,
                source_id=source["source_id"],
                filename=source["filename"],
                use_basis=source["use_basis"],
                allow_download=source["allow_download"],
                input_kind=source["input_kind"],
                upstream_source_id=source["upstream_source_id"],
                transformation_note=source["transformation_note"],
            )
        )
        import_quality.append(
            DatasetImportQuality(
                evaluation_id=pin.import_quality_evaluation_id,
                import_run_id=pin.import_run_id,
                outcome=pin.outcome,
                delivery_gate=pin.delivery_gate,
                rows_read=current.rows_read,
                rows_accepted=current.rows_accepted,
                rows_rejected=current.rows_rejected,
                rows_inserted=current.rows_inserted,
                rows_duplicate_identical=current.rows_duplicate_identical,
                rows_conflicted=current.rows_conflicted,
            )
        )
    if not specs or any(spec != specs[0] for spec in specs):
        raise ValueError("research requires one consistent original source/session declaration")
    spec = specs[0]
    if spec.availability_basis == "FINAL_REVISED":
        for observation in bars:
            if observation.available_at != observation.completed_at:
                raise ValueError(
                    f"FINAL_REVISED observation {observation.observation_id} must use exact bar "
                    "completion as its simulated available_at"
                )
    summary = _summary(resolved.manifest, partition)
    if (
        spec.interval != partition.interval
        or spec.exchange != summary.exchange
        or spec.product != summary.product
        or spec.symbol != summary.symbol
        or (spec.trading_day,) != summary.trading_days
        or spec.session_open != summary.session_open
        or spec.session_close != summary.session_close
        or spec.timezone != market.exchange_timezone
        or spec.currency != market.currency
        or spec.quantity_unit != market.quantity_unit
        or spec.price_tick != market.price_tick
        or spec.multiplier != market.multiplier
    ):
        raise ValueError("snapshot original source/session declaration differs from frozen meaning")
    expected = tuple(
        spec.session_open + spec.duration * offset
        for offset in range(int((spec.session_close - spec.session_open) / spec.duration))
    )
    if tuple(sorted(bar.event_time for bar in bars)) != expected:
        raise ValueError("research requires one complete continuous minute session")
    minute_pin = session.scalar(
        select(DatasetSnapshotSeriesQualityPin).where(
            DatasetSnapshotSeriesQualityPin.partition_id == partition.id
        )
    )
    if minute_pin is None or minute_pin.evaluation_scope != "MINUTE_SESSION_COVERAGE":
        raise ValueError("research requires pinned minute session quality evidence")
    coverage = minute_pin.quality_evaluation
    details = DatasetDetails(
        summary=summary,
        import_specs=(spec,),
        sources=tuple(sources),
        import_quality=tuple(import_quality),
        minute_quality=(
            DatasetMinuteQuality(
                evaluation_id=minute_pin.quality_evaluation_id,
                outcome=minute_pin.outcome,
                delivery_gate=minute_pin.delivery_gate,
                expected_observation_count=coverage.expected_observation_count,
                observed_count=coverage.covered_observation_count,
                missing_observation_count=coverage.missing_observation_count,
            ),
        ),
        available_at_cutoff=resolved.manifest.available_at_cutoff,
        volume_unit=partition.volume_unit,
        adjustment=partition.adjustment,
        timestamp_convention=partition.timestamp_convention,
        processing_provenance=stream_provenance,
    )
    return (
        ResearchDataset(
            snapshot_id,
            resolved.manifest.content_hash,
            market,
            tuple(bars),
            int(spec.duration.total_seconds()),
            details,
        ),
        details,
    )
