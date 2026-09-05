"""Import, quality-gate and pin one explicit intraday research dataset.

Callers supply market/session facts once. This Module owns catalog identity,
source receipts, publication and verified immutable reads; no ORM row crosses
its Interface. The current input is a complete one-minute DAY session whose
local calendar date is the declared trading day.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    CanonicalBar,
    DataSeries,
    DatasetSnapshotImportQualityPin,
    DatasetSnapshotManifest,
    DatasetSnapshotPartition,
    DatasetSnapshotSeriesQualityPin,
    Exchange,
    FuturesContract,
    FuturesProduct,
    ImportRun,
    TradingCalendar,
)
from northstar_quant.data.catalog.services import CatalogCommands
from northstar_quant.data.core.config import get_settings
from northstar_quant.data.ingestion.imports import (
    OhlcvImportCommand,
    OhlcvImportError,
    ParsedOhlcvRows,
    RawOhlcvRow,
    SourcePayload,
)
from northstar_quant.data.ingestion.service import OhlcvImportService
from northstar_quant.data.quality.evaluations import (
    ImportQualityEvaluationCommand,
    MinuteQualityEvaluationCommand,
)
from northstar_quant.data.quality.import_service import (
    ImportQualityEvaluationService,
    current_import_quality_state,
)
from northstar_quant.data.quality.minute_service import MinuteQualityEvaluationService
from northstar_quant.data.snapshots.publication import (
    PublishDatasetSnapshotCommand,
    SnapshotImportQualityPinSelection,
    SnapshotPartitionSelection,
)
from northstar_quant.data.snapshots.service import (
    DatasetSnapshotPublicationService,
    DatasetSnapshotResolutionService,
)


@dataclass(frozen=True, slots=True)
class ImportSpec:
    exchange: str
    symbol: str
    product: str
    timezone: str
    currency: str
    quantity_unit: str
    price_tick: Decimal
    multiplier: Decimal
    trading_day: date
    session_open: datetime
    session_close: datetime
    source_name: str
    source_reference: str
    availability_basis: str
    availability_note: str

    def __post_init__(self) -> None:
        for name in ("exchange", "symbol", "product", "quantity_unit"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,31}", value) is None
            ):
                raise ValueError(
                    f"data.{name} must be an uppercase identifier of at most 32 characters"
                )
        if (
            not isinstance(self.source_name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.source_name) is None
        ):
            raise ValueError(
                "data.source_name must be a source identifier of at most 64 characters"
            )
        for name in ("source_reference", "availability_note"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 1024
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"data.{name} must be nonempty text of at most 1024 characters")
        if not isinstance(self.availability_basis, str) or self.availability_basis not in {
            "SOURCE_DECLARED",
            "FINAL_REVISED",
            "SYNTHETIC",
        }:
            raise ValueError(
                "data.availability_basis must be SOURCE_DECLARED, FINAL_REVISED or SYNTHETIC"
            )
        if not isinstance(self.currency, str) or re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise ValueError("data.currency must be a three-letter uppercase currency")
        try:
            timezone = ZoneInfo(self.timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("data.timezone must name an IANA timezone") from error
        for name in ("price_tick", "multiplier"):
            value = getattr(self, name)
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or not Decimal(0) < value < Decimal("1000000000000")
                or int(value.as_tuple().exponent) < -12
            ):
                raise ValueError(f"data.{name} must be positive with at most 12 decimal places")
        if type(self.trading_day) is not date:
            raise ValueError("data.trading_day must be a date")
        for name in ("session_open", "session_close"):
            at = getattr(self, name)
            if (
                not isinstance(at, datetime)
                or at.utcoffset() != timedelta(0)
                or at.second != 0
                or at.microsecond != 0
                or at.astimezone(timezone).date() != self.trading_day
            ):
                raise ValueError(f"data.{name} must be UTC minute-aligned on the local trading day")
        if self.session_open >= self.session_close:
            raise ValueError("data.session_open must precede session_close")

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> ImportSpec:
        expected = {
            "exchange",
            "symbol",
            "product",
            "timezone",
            "currency",
            "quantity_unit",
            "price_tick",
            "multiplier",
            "trading_day",
            "session_open",
            "session_close",
            "source_name",
            "source_reference",
            "availability_basis",
            "availability_note",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("data input must contain exactly the current market/session fields")
        strings: dict[str, str] = {}
        for key, item in value.items():
            maximum = 1024 if key in {"source_reference", "availability_note"} else 128
            if not isinstance(item, str) or not item or len(item) > maximum:
                raise ValueError(f"data.{key} must be a bounded nonempty string")
            strings[key] = item
        for key in ("price_tick", "multiplier"):
            if re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?", strings[key]) is None:
                raise ValueError(f"data.{key} must be a plain positive decimal string")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", strings["trading_day"]) is None:
            raise ValueError("data.trading_day must use YYYY-MM-DD")
        return cls(
            exchange=strings["exchange"],
            symbol=strings["symbol"],
            product=strings["product"],
            timezone=strings["timezone"],
            currency=strings["currency"],
            quantity_unit=strings["quantity_unit"],
            price_tick=Decimal(strings["price_tick"]),
            multiplier=Decimal(strings["multiplier"]),
            trading_day=date.fromisoformat(strings["trading_day"]),
            session_open=_utc(strings["session_open"]),
            session_close=_utc(strings["session_close"]),
            source_name=strings["source_name"],
            source_reference=strings["source_reference"],
            availability_basis=strings["availability_basis"],
            availability_note=strings["availability_note"],
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "product": self.product,
            "timezone": self.timezone,
            "currency": self.currency,
            "quantity_unit": self.quantity_unit,
            "price_tick": format(self.price_tick.normalize(), "f"),
            "multiplier": format(self.multiplier.normalize(), "f"),
            "trading_day": self.trading_day.isoformat(),
            "session_open": self.session_open.isoformat().replace("+00:00", "Z"),
            "session_close": self.session_close.isoformat().replace("+00:00", "Z"),
            "source_name": self.source_name,
            "source_reference": self.source_reference,
            "availability_basis": self.availability_basis,
            "availability_note": self.availability_note,
        }


@dataclass(frozen=True, slots=True)
class Market:
    contract_id: UUID
    symbol: str
    exchange_timezone: str
    currency: str
    quantity_unit: str
    price_tick: Decimal
    multiplier: Decimal
    interval_seconds: int


@dataclass(frozen=True, slots=True)
class ResearchBar:
    observation_id: UUID
    event_time: datetime
    completed_at: datetime
    available_at: datetime
    trading_day: date
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class ResearchDataset:
    snapshot_id: UUID
    content_hash: str
    market: Market
    bars: tuple[ResearchBar, ...]
    # Pure in-memory calculations need no persisted source receipt. Every Data
    # read/import supplies verified details; absence is never historical evidence.
    details: DatasetDetails | None = None


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    """A cheap publication listing; opening or running still verifies its evidence."""

    snapshot_id: UUID
    content_hash: str
    exchange: str
    product: str
    symbol: str
    trading_day: date
    session_open: datetime
    session_close: datetime
    bar_count: int
    published_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": str(self.snapshot_id),
            "content_hash": self.content_hash,
            "exchange": self.exchange,
            "product": self.product,
            "symbol": self.symbol,
            "trading_day": self.trading_day.isoformat(),
            "session_open": _timestamp(self.session_open),
            "session_close": _timestamp(self.session_close),
            "bar_count": self.bar_count,
            "published_at": _timestamp(self.published_at),
        }


@dataclass(frozen=True, slots=True)
class DatasetSource:
    import_run_id: UUID
    receipt_id: UUID
    source_name: str
    content_hash: str
    byte_count: int
    received_at: datetime
    acquisition_use: str
    redistribution_policy: str
    retention_policy: str
    source_id: UUID
    filename: str
    use_basis: str
    allow_download: bool
    input_kind: str
    upstream_source_id: UUID | None
    transformation_note: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "import_run_id": str(self.import_run_id),
            "receipt_id": str(self.receipt_id),
            "received_at": _timestamp(self.received_at),
            "source_id": str(self.source_id),
            "upstream_source_id": (
                None if self.upstream_source_id is None else str(self.upstream_source_id)
            ),
        }


@dataclass(frozen=True, slots=True)
class DatasetImportQuality:
    evaluation_id: UUID
    import_run_id: UUID
    outcome: str
    delivery_gate: str
    rows_read: int
    rows_accepted: int
    rows_rejected: int
    rows_inserted: int
    rows_duplicate_identical: int
    rows_conflicted: int

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "evaluation_id": str(self.evaluation_id),
            "import_run_id": str(self.import_run_id),
        }


@dataclass(frozen=True, slots=True)
class DatasetMinuteQuality:
    evaluation_id: UUID
    outcome: str
    delivery_gate: str
    expected_observation_count: int
    observed_count: int
    missing_observation_count: int

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "evaluation_id": str(self.evaluation_id)}


@dataclass(frozen=True, slots=True)
class DatasetDetails:
    """Verified publication meaning, including limitations of the supplied evidence."""

    summary: DatasetSummary
    import_spec: ImportSpec
    sources: tuple[DatasetSource, ...]
    import_quality: tuple[DatasetImportQuality, ...]
    minute_quality: DatasetMinuteQuality
    available_at_cutoff: datetime
    volume_unit: str
    adjustment: str
    timestamp_convention: str

    @property
    def limitations(self) -> tuple[str, ...]:
        availability = {
            "SOURCE_DECLARED": (
                "Availability timestamps are supplied by the operator; historical publication "
                "has not been independently verified."
            ),
            "FINAL_REVISED": (
                "Final revised data is for retrospective exploration. available_at is an "
                "explicit simulated information-clock assumption at exact bar completion, "
                "not historical publication."
            ),
            "SYNTHETIC": "Synthetic observations are for engineering checks only.",
        }[self.import_spec.availability_basis]
        return (
            availability,
            "Source references and notes are operator declarations, not verified acquisition "
            "or redistribution rights.",
            "The exact received CSV bytes are retained in the managed source archive. "
            "A converted CSV is not an upstream provider response; only declared, linked "
            "upstream files constitute retained upstream evidence.",
            "Receipt received_at is local ingestion metadata, not historical publication time.",
            "Research currently supports one contract and one continuous DAY session on one "
            "local trading day; it does not replay revisions or perform daily settlement.",
        )

    def to_dict(self) -> dict[str, object]:
        spec = self.import_spec
        return {
            **self.summary.to_dict(),
            "import_spec": spec.to_mapping(),
            "sources": [source.to_dict() for source in self.sources],
            "quality": {
                "imports": [quality.to_dict() for quality in self.import_quality],
                "minute": self.minute_quality.to_dict(),
            },
            "semantics": {
                "interval_seconds": 60,
                "timestamp_convention": self.timestamp_convention,
                "adjustment": self.adjustment,
                "timezone": spec.timezone,
                "currency": spec.currency,
                "quantity_unit": spec.quantity_unit,
                "volume_unit": self.volume_unit,
                "price_tick": format(spec.price_tick.normalize(), "f"),
                "multiplier": format(spec.multiplier.normalize(), "f"),
                "available_at_cutoff": _timestamp(self.available_at_cutoff),
            },
            "source_reference": spec.source_reference,
            "availability_basis": spec.availability_basis,
            "availability_note": spec.availability_note,
            "limitations": list(self.limitations),
        }


def _import_csv(
    engine: Engine,
    content: bytes,
    spec: ImportSpec,
    *,
    archive: dict[str, object],
    processing_hash: str,
    stage: Callable[[str, dict[str, object]], None],
) -> ResearchDataset:
    """Private canonical pipeline; DataLibrary owns admission, archive and attempts."""

    payload = SourcePayload(content, hashlib.sha256(content).hexdigest(), len(content))
    adapter = _ResearchCsv(spec, payload=payload, archive=archive)
    # Validate the whole input before catalog registration or canonical writes.
    # Existing observations must never fill gaps in the operator's current file.
    stage("PARSING", {})
    adapter.parse(payload, source_timezone_name=spec.timezone)
    identity = _digest({"processing_hash": processing_hash, "archive": archive})
    key = "research-" + identity
    with Session(engine) as session:
        previous = session.scalar(
            select(DatasetSnapshotManifest.id).where(DatasetSnapshotManifest.idempotency_key == key)
        )
    if previous is not None:
        return _load_dataset(engine, previous)
    stage("IMPORTING", {})
    series_id = _catalog(engine, spec)
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        imported = OhlcvImportService(session, adapter=adapter).import_file(
            OhlcvImportCommand(
                # The ingestion adapter already owns the exact archived bytes;
                # this diagnostic name is never opened or interpreted as a path.
                file_path=Path("received.csv"),
                series_id=series_id,
                source_name=spec.source_name,
                source_timezone_name=spec.timezone,
                idempotency_key=key,
                correlation_id=key,
            )
        )
        stage(
            "IMPORTING",
            {
                "import_run_id": str(imported.import_run_id),
                "import_status": imported.status,
                "rows_read": imported.rows_read,
                "rows_accepted": imported.rows_accepted,
                "rows_rejected": imported.rows_rejected,
            },
        )
        if imported.status != "SUCCEEDED":
            failure = session.get(ImportRun, imported.import_run_id)
            detail = "unknown rejection" if failure is None else failure.error_detail
            raise ValueError(
                f"data import {imported.status}: {imported.rows_rejected} rejected rows; {detail}"
            )
    with Session(engine) as session:
        facts = session.execute(
            select(CanonicalBar.import_run_id, CanonicalBar.available_at).where(
                CanonicalBar.series_id == series_id,
                CanonicalBar.trading_day == spec.trading_day,
            )
        ).all()
        if not facts:
            raise ValueError("the import produced no observations for the declared session")
        cutoff = max(row.available_at for row in facts)
        import_ids = sorted({row.import_run_id for row in facts}, key=str)
    stage("QUALITY", {"import_run_id": str(imported.import_run_id)})
    pins: list[SnapshotImportQualityPinSelection] = []
    for import_id in import_ids:
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            quality = ImportQualityEvaluationService(session).evaluate(
                ImportQualityEvaluationCommand(
                    import_run_id=import_id,
                    idempotency_key="research-import-" + str(import_id),
                    correlation_id="research-import-" + str(import_id),
                )
            )
            if quality.delivery_gate != "ELIGIBLE":
                raise ValueError(f"import quality rejected delivery: {quality.outcome}")
            pins.append(
                SnapshotImportQualityPinSelection(import_id, quality.import_quality_evaluation_id)
            )
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        coverage = MinuteQualityEvaluationService(session).evaluate(
            MinuteQualityEvaluationCommand(
                series_id=series_id,
                from_trading_day=spec.trading_day,
                to_trading_day=spec.trading_day,
                as_of=cutoff,
                idempotency_key=key,
                correlation_id=key,
            )
        )
        if coverage.delivery_gate != "ELIGIBLE":
            raise ValueError(
                f"session quality rejected delivery: {coverage.outcome}; "
                f"{coverage.missing_observation_count} missing bars"
            )
    stage("PUBLISHING", {"minute_evaluation_id": str(coverage.quality_evaluation_id)})
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        published = DatasetSnapshotPublicationService(session).publish(
            PublishDatasetSnapshotCommand(
                available_at_cutoff=cutoff,
                partitions=(
                    SnapshotPartitionSelection(
                        series_id,
                        spec.trading_day,
                        spec.trading_day,
                        coverage.quality_evaluation_id,
                    ),
                ),
                import_quality_pins=tuple(pins),
                idempotency_key=key,
                correlation_id=key,
            )
        )
    return _load_dataset(engine, published.snapshot_id)


def _load_dataset(engine: Engine, snapshot_id: UUID) -> ResearchDataset:
    """Verify observations and their original source evidence in one read transaction."""

    with Session(engine, autoflush=False, expire_on_commit=False) as session:
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
        trading_day=partition.trading_day_from,
        session_open=partition.event_time_from,
        session_close=partition.event_time_to + timedelta(minutes=1),
        bar_count=partition.row_count,
        published_at=manifest.created_at,
    )


def _read_dataset(session: Session, snapshot_id: UUID) -> tuple[ResearchDataset, DatasetDetails]:
    resolved = DatasetSnapshotResolutionService(session).resolve(snapshot_id)
    partitions = session.scalars(
        select(DatasetSnapshotPartition).where(DatasetSnapshotPartition.manifest_id == snapshot_id)
    ).all()
    if len(partitions) != 1:
        raise ValueError("research requires exactly one immutable snapshot partition")
    partition = partitions[0]
    if partition.interval != "1m" or partition.timestamp_convention != "BAR_START":
        raise ValueError("research requires one-minute BAR_START data")
    market = Market(
        contract_id=partition.contract_id,
        symbol=partition.contract_code,
        exchange_timezone=partition.exchange_timezone_name,
        currency=partition.price_currency,
        quantity_unit=partition.quantity_unit,
        price_tick=partition.price_tick,
        multiplier=partition.contract_multiplier,
        interval_seconds=60,
    )
    bars: list[ResearchBar] = []
    member_import_ids: set[UUID] = set()
    for member in resolved.members:
        bar = member.canonical_bar
        if bar.revision_number != 1 or bar.supersedes_canonical_bar_id is not None:
            raise ValueError("research does not replay observation corrections")
        if bar.import_run_id is None:
            raise ValueError("research requires original import evidence for every observation")
        member_import_ids.add(bar.import_run_id)
        completed = member.event_time + timedelta(minutes=1)
        if member.available_at < completed:
            raise ValueError("a bar cannot be available before its completion")
        bars.append(
            ResearchBar(
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
    pins = session.scalars(
        select(DatasetSnapshotImportQualityPin)
        .where(DatasetSnapshotImportQualityPin.manifest_id == snapshot_id)
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
            or imported.mapping_version != _ResearchCsv.mapping_version
        ):
            raise ValueError("snapshot source mapping is missing, unsupported or has drifted")
        source_spec = mapping.get("session")
        if not isinstance(source_spec, dict):
            raise ValueError("snapshot original source session metadata is missing")
        spec = ImportSpec.from_mapping(source_spec)
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
        archive = mapping.get("archive")
        if not isinstance(archive, dict):
            raise ValueError("snapshot original source archive identity is missing")
        source = (
            session.execute(
                text("SELECT * FROM data_sources WHERE source_id = :source_id"),
                {"source_id": UUID(str(archive.get("source_id")))},
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
        spec.exchange != summary.exchange
        or spec.product != summary.product
        or spec.symbol != summary.symbol
        or spec.trading_day != summary.trading_day
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
        spec.session_open + timedelta(minutes=offset)
        for offset in range(int((spec.session_close - spec.session_open).total_seconds()) // 60)
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
        import_spec=spec,
        sources=tuple(sources),
        import_quality=tuple(import_quality),
        minute_quality=DatasetMinuteQuality(
            evaluation_id=minute_pin.quality_evaluation_id,
            outcome=minute_pin.outcome,
            delivery_gate=minute_pin.delivery_gate,
            expected_observation_count=coverage.expected_observation_count,
            observed_count=coverage.covered_observation_count,
            missing_observation_count=coverage.missing_observation_count,
        ),
        available_at_cutoff=resolved.manifest.available_at_cutoff,
        volume_unit=partition.volume_unit,
        adjustment=partition.adjustment,
        timestamp_convention=partition.timestamp_convention,
    )
    return (
        ResearchDataset(snapshot_id, resolved.manifest.content_hash, market, tuple(bars), details),
        details,
    )


def _catalog(engine: Engine, spec: ImportSpec) -> UUID:
    commands = CatalogCommands()
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        # Catalog registration is rare and local; one transaction-scoped lock
        # prevents concurrent first-run duplicate identities without retry layers.
        session.execute(text("SELECT pg_advisory_xact_lock(728401927)")).scalar_one()
        exchange = session.scalar(select(Exchange).where(Exchange.code == spec.exchange))
        if exchange is None:
            exchange = commands.register_exchange(
                session, code=spec.exchange, name=spec.exchange, timezone_name=spec.timezone
            )
        if exchange.timezone_name != spec.timezone:
            raise ValueError("exchange timezone differs from its registered identity")
        product = session.scalar(
            select(FuturesProduct).where(
                FuturesProduct.exchange_id == exchange.id,
                FuturesProduct.code == spec.product,
            )
        )
        if product is None:
            product = commands.register_product(
                session,
                exchange_id=exchange.id,
                code=spec.product,
                name=spec.product,
                price_tick=spec.price_tick,
                contract_multiplier=spec.multiplier,
                quantity_unit=spec.quantity_unit,
                currency=spec.currency,
            )
        if (
            product.price_tick,
            product.contract_multiplier,
            product.quantity_unit,
            product.currency,
        ) != (
            spec.price_tick,
            spec.multiplier,
            spec.quantity_unit,
            spec.currency,
        ):
            raise ValueError("market economics differ from the registered product")
        contract = session.scalar(
            select(FuturesContract).where(
                FuturesContract.product_id == product.id,
                FuturesContract.contract_code == spec.symbol,
            )
        )
        if contract is None:
            contract = commands.register_contract(
                session, product_id=product.id, contract_code=spec.symbol
            )
        calendar_code = (
            "SESSION-"
            + _digest(
                {
                    "timezone": spec.timezone,
                    "day": spec.trading_day.isoformat(),
                    "open": spec.session_open.isoformat(),
                    "close": spec.session_close.isoformat(),
                }
            )[:24].upper()
        )
        calendar = session.scalar(
            select(TradingCalendar).where(
                TradingCalendar.exchange_id == exchange.id,
                TradingCalendar.code == calendar_code,
                TradingCalendar.revision == 1,
            )
        )
        if calendar is None:
            calendar = commands.register_calendar(
                session,
                exchange_id=exchange.id,
                code=calendar_code,
                revision=1,
                timezone_name=spec.timezone,
            )
            commands.register_trading_day(
                session, calendar_id=calendar.id, trading_day=spec.trading_day, status="OPEN"
            )
            commands.register_session(
                session,
                calendar_id=calendar.id,
                trading_day=spec.trading_day,
                sequence=0,
                kind="DAY",
                opens_at=spec.session_open,
                closes_at=spec.session_close,
            )
        series = session.scalar(
            select(DataSeries).where(
                DataSeries.contract_id == contract.id,
                DataSeries.calendar_id == calendar.id,
                DataSeries.interval == "1m",
                DataSeries.kind == "OHLCV",
                DataSeries.adjustment == "RAW",
            )
        )
        if series is None:
            scale = max(0, -int(spec.price_tick.normalize().as_tuple().exponent))
            series = commands.register_data_series(
                session,
                contract_id=contract.id,
                calendar_id=calendar.id,
                interval="1m",
                price_scale=scale,
                quantity_scale=0,
                volume_unit="LOT",
                turnover_currency=spec.currency,
            )
        series_id = series.id
        session.commit()
        return series_id


class _ResearchCsv:
    """The current eight-column research source; market facts belong to ImportSpec."""

    media_type = "text/csv"
    mapping_version = "research-session-csv/1"
    job_kind = "RESEARCH_CSV_IMPORT"
    input_kind = "OPERATOR_FILE"
    retention_policy = "CONTROLLED"
    redistribution_policy = "PROHIBITED"

    def __init__(
        self, spec: ImportSpec, *, payload: SourcePayload, archive: dict[str, object]
    ) -> None:
        self.spec = spec
        self.payload = payload
        self.archive = archive
        self.acquisition_use = (
            "SYNTHETIC_TEST_ONLY"
            if spec.availability_basis == "SYNTHETIC"
            else "PRIVATE_RESEARCH_ONLY"
        )
        self._parsed: ParsedOhlcvRows | None = None

    def load(self, file_path: Path) -> SourcePayload:
        del file_path
        return self.payload

    def parse(self, payload: SourcePayload, *, source_timezone_name: str) -> ParsedOhlcvRows:
        if source_timezone_name != self.spec.timezone:
            raise ValueError("source timezone differs from the declared session")
        if payload is self.payload and self._parsed is not None:
            return self._parsed
        columns = {
            "event_time",
            "available_at",
            "source_record_id",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }
        rows: list[RawOhlcvRow] = []
        settings = get_settings()
        try:
            reader = csv.DictReader(io.StringIO(payload.content.decode("utf-8")), strict=True)
            if (
                reader.fieldnames is None
                or len(reader.fieldnames) != len(columns)
                or set(reader.fieldnames) != columns
            ):
                raise ValueError(
                    "CSV columns must be exactly event_time,available_at,source_record_id,"
                    "open,high,low,close,volume"
                )
            for index, row in enumerate(reader, start=2):
                if len(rows) >= settings.max_csv_rows:
                    raise ValueError("CSV exceeds the configured row limit")
                if set(row) != columns or any(
                    not isinstance(v, str) or not v or len(v) > settings.max_csv_field_bytes
                    for v in row.values()
                ):
                    raise ValueError(f"CSV row {index} contains missing, extra or oversized fields")
                event = _utc(row["event_time"])
                available = _utc(row["available_at"])
                if (
                    self.spec.availability_basis == "FINAL_REVISED"
                    and available != event + timedelta(minutes=1)
                ):
                    raise ValueError(
                        f"CSV row {index} FINAL_REVISED available_at must equal event_time "
                        "+ 1 minute (the simulated information-clock assumption)"
                    )
                if not self.spec.session_open <= event < self.spec.session_close:
                    raise ValueError(f"CSV row {index} falls outside the declared session")
                numbers: dict[str, Decimal] = {}
                for name in ("open", "high", "low", "close", "volume"):
                    if re.fullmatch(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,12})?", row[name]) is None:
                        raise ValueError(
                            f"CSV row {index} {name} must be a nonnegative plain decimal"
                        )
                    numbers[name] = Decimal(row[name])
                rows.append(
                    RawOhlcvRow(
                        source_row_number=index,
                        symbol=self.spec.symbol,
                        interval="1m",
                        event_time=event.astimezone(ZoneInfo(self.spec.timezone)),
                        trading_day=self.spec.trading_day,
                        available_at=available.astimezone(ZoneInfo(self.spec.timezone)),
                        source_record_id=row["source_record_id"],
                        price_currency=self.spec.currency,
                        volume_unit="LOT",
                        open_interest_unit="LOT",
                        turnover_currency=self.spec.currency,
                        turnover_multiplier=Decimal(1),
                        open_price=numbers["open"],
                        high_price=numbers["high"],
                        low_price=numbers["low"],
                        close_price=numbers["close"],
                        volume=numbers["volume"],
                        turnover=None,
                        open_interest=None,
                    )
                )
            if not rows:
                raise ValueError("CSV contains no observations")
            expected = tuple(
                self.spec.session_open + timedelta(minutes=offset)
                for offset in range(
                    int((self.spec.session_close - self.spec.session_open).total_seconds()) // 60
                )
            )
            actual = sorted(row.event_time for row in rows if row.event_time is not None)
            if tuple(actual) != expected:
                missing = len(set(expected).difference(actual))
                repeated = len(actual) - len(set(actual))
                raise ValueError(
                    "CSV must contain exactly one bar for each declared session minute; "
                    f"{missing} missing bars and {repeated} repeated event times"
                )
        except (UnicodeError, ValueError, csv.Error) as error:
            raise OhlcvImportError("RESEARCH_CSV_REJECTED", str(error)) from error
        self.payload = payload
        self._parsed = ParsedOhlcvRows(
            tuple(rows), self.mapping_metadata(source_timezone_name=source_timezone_name)
        )
        return self._parsed

    def mapping_metadata(self, *, source_timezone_name: str) -> dict[str, object]:
        return {
            "mapping_version": self.mapping_version,
            "source_timezone_name": source_timezone_name,
            "session": self.spec.to_mapping(),
            "archive": self.archive,
        }

    def request_fingerprint_metadata(self, *, source_timezone_name: str) -> dict[str, object]:
        return self.mapping_metadata(source_timezone_name=source_timezone_name)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source_evidence(source: Mapping[str, object]) -> dict[str, object]:
    """The immutable archive declaration pinned by an actual canonical import."""

    return {
        key: (
            _timestamp(value)
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
        for key, value in source.items()
        if key
        in {
            "source_id",
            "filename",
            "source_name",
            "use_basis",
            "allow_retention",
            "allow_download",
            "input_kind",
            "upstream_source_id",
            "transformation_note",
            "upstream_evidence_hash",
            "content_hash",
            "byte_count",
            "received_at",
        }
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
        raise ValueError("session timestamps must use YYYY-MM-DDTHH:MM:SSZ")
    return datetime.fromisoformat(value).astimezone(UTC)
