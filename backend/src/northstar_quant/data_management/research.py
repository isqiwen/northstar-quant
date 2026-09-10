"""Import, quality-gate and pin one explicit intraday research dataset.

Callers supply market/session facts once. This Module owns catalog identity,
source receipts, publication and verified immutable reads; no ORM row crosses
its Interface. The current input is one complete one-minute DAY or NIGHT session
with an explicitly declared trading day; calendar holidays are not inferred.
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
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from northstar_quant.data_management.broker import verify_broker_contract
from northstar_quant.data_management.catalog.models import (
    CanonicalBar,
    DataSeries,
    DatasetSnapshotManifest,
    Exchange,
    FuturesContract,
    FuturesProduct,
    ImportRun,
    TradingCalendar,
)
from northstar_quant.data_management.catalog.services import CatalogCommands
from northstar_quant.data_management.core.config import get_settings
from northstar_quant.data_management.ingestion.imports import (
    OhlcvImportCommand,
    OhlcvImportError,
    ParsedOhlcvRows,
    RawOhlcvRow,
    SourcePayload,
)
from northstar_quant.data_management.ingestion.service import OhlcvImportService
from northstar_quant.data_management.quality.evaluations import (
    ImportQualityEvaluationCommand,
    MinuteQualityEvaluationCommand,
)
from northstar_quant.data_management.quality.import_service import (
    ImportQualityEvaluationService,
)
from northstar_quant.data_management.quality.minute_service import MinuteQualityEvaluationService
from northstar_quant.data_management.research_input import ImportSpec
from northstar_quant.data_management.snapshots.publication import (
    PublishDatasetSnapshotCommand,
    SnapshotImportQualityPinSelection,
    SnapshotPartitionSelection,
)
from northstar_quant.data_management.snapshots.service import (
    DatasetSnapshotPublicationService,
)
from northstar_quant.market_data import Market, MarketBar

if TYPE_CHECKING:
    from northstar_quant.data_management.stream import StreamMinutes


@dataclass(frozen=True, slots=True)
class ResearchDataset:
    snapshot_id: UUID
    content_hash: str
    market: Market
    bars: tuple[MarketBar, ...]
    # Pure in-memory calculations need no persisted source receipt. Every Data
    # read/import supplies verified details; absence is never historical evidence.
    details: DatasetDetails | None = None


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    """Fixed publication extent; declared sessions retain their own trading days."""

    snapshot_id: UUID
    content_hash: str
    exchange: str
    product: str
    symbol: str
    trading_days: tuple[date, ...]
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
            "trading_days": [day.isoformat() for day in self.trading_days],
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
    import_specs: tuple[ImportSpec, ...]
    sources: tuple[DatasetSource, ...]
    import_quality: tuple[DatasetImportQuality, ...]
    minute_quality: tuple[DatasetMinuteQuality, ...]
    available_at_cutoff: datetime
    volume_unit: str
    adjustment: str
    timestamp_convention: str
    processing_provenance: dict[str, object] | None = None

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
            "LOCAL_CAPTURE_RECONSTRUCTED": (
                "Minutes are reconstructed from retained local callback receipt times, not "
                "exchange publication or proof that this calculation ran at that time. "
                "Shadow pauses are not replayed and historical shadow decisions are not changed."
            ),
        }[self.import_specs[0].availability_basis]
        return (
            availability,
            "Source references and notes are operator declarations, not verified acquisition "
            "or redistribution rights.",
            "The retained CTP JSON prefix contains copied SDK callbacks, not vendor wire bytes. "
            "Observed LastPrice OHLC and snapshot-timed cumulative volume differences are "
            "not exact trade-tape OHLCV; SimNow or synthetic callbacks do not prove "
            "live acceptance."
            if self.processing_provenance is not None
            else "The exact received CSV bytes are retained in the managed source archive. "
            "A converted CSV is not an upstream provider response; only declared, linked "
            "upstream files constitute retained upstream evidence.",
            "Receipt received_at is local ingestion metadata, not historical publication time.",
            "This snapshot contains one contract and explicitly declared DAY/NIGHT sessions "
            "with their own trading days; calendar/holiday attribution is source-declared, "
            "not independently verified. It does not perform daily settlement.",
        )

    def to_dict(self) -> dict[str, object]:
        spec = self.import_specs[0]
        return {
            **self.summary.to_dict(),
            "import_specs": [item.to_mapping() for item in self.import_specs],
            "sources": [source.to_dict() for source in self.sources],
            "quality": {
                "imports": [quality.to_dict() for quality in self.import_quality],
                "minutes": [item.to_dict() for item in self.minute_quality],
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
            "source_reference": "; ".join(
                dict.fromkeys(item.source_reference for item in self.import_specs)
            ),
            "availability_basis": spec.availability_basis,
            "availability_note": "; ".join(
                dict.fromkeys(item.availability_note for item in self.import_specs)
            ),
            **(
                {"processing_provenance": self.processing_provenance}
                if self.processing_provenance is not None
                else {}
            ),
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
    if spec.availability_basis == "LOCAL_CAPTURE_RECONSTRUCTED":
        raise ValueError("local capture timing requires the original retained CTP JSON prefix")
    adapter = _ResearchCsv(spec, payload=payload, archive=archive)
    return _import_market(engine, spec, adapter, processing_hash=processing_hash, stage=stage)


def _import_stream(
    engine: Engine,
    content: bytes,
    *,
    parameters: dict[str, object],
    archive: dict[str, object],
    processing_hash: str,
    stage: Callable[[str, dict[str, object]], None],
) -> ResearchDataset:
    from northstar_quant.data_management.stream import reconstruct_stream

    stage("PARSING", {})
    reconstructed = reconstruct_stream(content, parameters)
    binding = reconstructed.binding
    contract_id = UUID(binding["contract_id"])
    verify_broker_contract(engine, contract_id, binding["terms"])
    with Session(engine) as session:
        contract = session.get(FuturesContract, contract_id)
        if contract is None:
            raise ValueError("CTP market contract is no longer registered")
        product = contract.product
        spec = ImportSpec(
            exchange="SHFE",
            symbol=contract.contract_code,
            product=product.code,
            timezone="Asia/Shanghai",
            currency=product.currency,
            quantity_unit=product.quantity_unit,
            price_tick=product.price_tick,
            multiplier=product.contract_multiplier,
            trading_day=reconstructed.session_open.astimezone(ZoneInfo("Asia/Shanghai")).date(),
            session_kind="DAY",
            session_open=reconstructed.session_open,
            session_close=reconstructed.session_close,
            source_name="SIMNOW_CTP",
            source_reference=(
                f"Retained SimNow CTP stream {reconstructed.stream_id}; "
                f"prefix 1..{reconstructed.through_sequence}; "
                f"original JSON sha256 {hashlib.sha256(content).hexdigest()}"
            ),
            availability_basis="LOCAL_CAPTURE_RECONSTRUCTED",
            availability_note=(
                "Reconstructed from original local callback receipt times; no exchange publication "
                "or historical shadow-decision claim. Observed LastPrice OHLC and cumulative "
                "volume deltas assigned by snapshot time are not trade-tape OHLCV."
            ),
        )
    payload = SourcePayload(content, hashlib.sha256(content).hexdigest(), len(content))
    adapter = _CtpSegment(spec, payload=payload, archive=archive, reconstructed=reconstructed)
    return _import_market(engine, spec, adapter, processing_hash=processing_hash, stage=stage)


def _import_market(
    engine: Engine,
    spec: ImportSpec,
    adapter: _ResearchCsv,
    *,
    processing_hash: str,
    stage: Callable[[str, dict[str, object]], None],
) -> ResearchDataset:
    payload, archive = adapter.payload, adapter.archive
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
    from .research_reader import load_dataset

    if previous is not None:
        return load_dataset(engine, previous)
    stage("IMPORTING", {})
    series_id = _catalog(engine, spec)
    # A sampled CTP market is not interchangeable with operator OHLCV, even when
    # prices happen to match. The current catalog has one meaning per series;
    # refuse mixed fixed inputs rather than inventing a source-specific calendar.
    with Session(engine) as session:
        previous_imports = session.scalars(
            select(ImportRun).where(
                ImportRun.id.in_(
                    select(CanonicalBar.import_run_id).where(CanonicalBar.series_id == series_id)
                )
            )
        ).all()
        for previous_import in previous_imports:
            if _CtpSegment.mapping_version in {
                adapter.mapping_version,
                previous_import.mapping_version,
            } and (
                previous_import.mapping_version != adapter.mapping_version
                or previous_import.source_receipt is None
                or previous_import.source_receipt.content_hash != payload.content_hash
                or previous_import.mapping is None
                or previous_import.mapping.get("session") != spec.to_mapping()
            ):
                raise ValueError(
                    "CTP observed-minute series cannot mix different sampling semantics "
                    "or fixed sources"
                )
    with Session(
        engine.execution_options(live_write=True) if engine.dialect.name == "sqlite" else engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
        imported = OhlcvImportService(session, adapter=adapter).import_file(
            OhlcvImportCommand(
                # The ingestion adapter already owns the exact archived bytes;
                # this diagnostic name is never opened or interpreted as a path.
                file_path=Path(
                    "received.json" if isinstance(adapter, _CtpSegment) else "received.csv"
                ),
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
        with Session(
            engine.execution_options(live_write=True)
            if engine.dialect.name == "sqlite"
            else engine,
            autoflush=False,
            expire_on_commit=False,
        ) as session:
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
    with Session(
        engine.execution_options(live_write=True) if engine.dialect.name == "sqlite" else engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
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
    with Session(
        engine.execution_options(live_write=True) if engine.dialect.name == "sqlite" else engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
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
    return load_dataset(engine, published.snapshot_id)


def _catalog(engine: Engine, spec: ImportSpec) -> UUID:
    commands = CatalogCommands()
    with Session(
        engine.execution_options(live_write=True) if engine.dialect.name == "sqlite" else engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
        # Catalog registration is rare and local; one transaction-scoped lock
        # prevents concurrent first-run duplicate identities without retry layers.
        if engine.dialect.name == "postgresql":
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
                    "kind": spec.session_kind,
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
                kind=spec.session_kind,
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
    mapping_version = "research-session-csv/2"
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


class _CtpSegment(_ResearchCsv):
    """Actual JSON input and sampled minute output share the canonical import behavior."""

    media_type = "application/json"
    mapping_version = "ctp-callback-minute/2"
    job_kind = "CTP_MINUTE_IMPORT"
    input_kind = "CTP_CALLBACK_SEGMENT"

    def __init__(
        self,
        spec: ImportSpec,
        *,
        payload: SourcePayload,
        archive: dict[str, object],
        reconstructed: StreamMinutes,
    ) -> None:
        super().__init__(spec, payload=payload, archive=archive)
        self.reconstructed = reconstructed

    def parse(self, payload: SourcePayload, *, source_timezone_name: str) -> ParsedOhlcvRows:
        if payload != self.payload or source_timezone_name != self.spec.timezone:
            raise ValueError("CTP parser input differs from the retained callback prefix")
        if self._parsed is not None:
            return self._parsed
        timezone = ZoneInfo(self.spec.timezone)
        self._parsed = ParsedOhlcvRows(
            tuple(
                RawOhlcvRow(
                    source_row_number=index,
                    symbol=self.spec.symbol,
                    interval="1m",
                    event_time=_utc(bar["start_at"]).astimezone(timezone),
                    trading_day=self.spec.trading_day,
                    available_at=_utc(bar["available_at"]).astimezone(timezone),
                    source_record_id=f"{self.reconstructed.stream_id}:{bar['observation_id']}",
                    price_currency=self.spec.currency,
                    volume_unit="LOT",
                    open_interest_unit="LOT",
                    turnover_currency=self.spec.currency,
                    turnover_multiplier=Decimal(1),
                    open_price=Decimal(bar["open"]),
                    high_price=Decimal(bar["high"]),
                    low_price=Decimal(bar["low"]),
                    close_price=Decimal(bar["close"]),
                    volume=Decimal(bar["volume"]),
                    turnover=None,
                    open_interest=None,
                )
                for index, bar in enumerate(self.reconstructed.bars, 1)
            ),
            self.mapping_metadata(source_timezone_name=source_timezone_name),
        )
        return self._parsed

    def mapping_metadata(self, *, source_timezone_name: str) -> dict[str, object]:
        return {
            **super().mapping_metadata(source_timezone_name=source_timezone_name),
            "stream": self.reconstructed.provenance(),
        }


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
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None:
        raise ValueError("timestamps must use UTC Z with at most six fractional second digits")
    return datetime.fromisoformat(value).astimezone(UTC)
