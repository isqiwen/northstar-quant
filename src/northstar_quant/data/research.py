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
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    SOURCE_RECEIPT_DEFAULT_ACQUISITION_USE,
    SOURCE_RECEIPT_DEFAULT_REDISTRIBUTION_POLICY,
    CanonicalBar,
    DataSeries,
    DatasetSnapshotManifest,
    DatasetSnapshotPartition,
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
from northstar_quant.data.quality.import_service import ImportQualityEvaluationService
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
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("data input must contain exactly the current market/session fields")
        strings: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(item, str) or not item or len(item) > 128:
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


def import_csv(engine: Engine, path: Path, spec: ImportSpec) -> ResearchDataset:
    """Durably accept a complete session and return its verified immutable snapshot.

    An exact retry reuses the imported rows, quality evidence and snapshot.
    Failed quality remains recorded but cannot produce a ResearchDataset.
    """

    if not isinstance(spec, ImportSpec):
        raise ValueError("spec must be an ImportSpec")
    adapter = _ResearchCsv(spec)
    payload = adapter.load(path)
    # Validate the whole input before catalog registration or canonical writes.
    # Existing observations must never fill gaps in the operator's current file.
    adapter.parse(payload, source_timezone_name=spec.timezone)
    identity = _digest({"source_hash": payload.content_hash, "spec": spec.to_mapping()})
    key = "research-" + identity
    with Session(engine) as session:
        previous = session.scalar(
            select(DatasetSnapshotManifest.id).where(DatasetSnapshotManifest.idempotency_key == key)
        )
    if previous is not None:
        return load_dataset(engine, previous)
    series_id = _catalog(engine, spec)
    # Reuse exactly the bounded bytes whose identity we pinned, even if the
    # operator edits the source file while this run is executing.
    adapter.payload = payload
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        imported = OhlcvImportService(session, adapter=adapter).import_file(
            OhlcvImportCommand(
                file_path=path,
                series_id=series_id,
                source_name=spec.source_name,
                source_timezone_name=spec.timezone,
                idempotency_key=key,
                correlation_id=key,
            )
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
    return load_dataset(engine, published.snapshot_id)


def load_dataset(engine: Engine, snapshot_id: UUID) -> ResearchDataset:
    """Verify stored identity and return native values from one pinned session."""

    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        resolved = DatasetSnapshotResolutionService(session).resolve(snapshot_id)
        partitions = session.scalars(
            select(DatasetSnapshotPartition).where(
                DatasetSnapshotPartition.manifest_id == snapshot_id
            )
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
        for member in resolved.members:
            bar = member.canonical_bar
            if bar.revision_number != 1 or bar.supersedes_canonical_bar_id is not None:
                raise ValueError("research does not replay observation corrections")
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
        return ResearchDataset(snapshot_id, resolved.manifest.content_hash, market, tuple(bars))


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
    retention_policy = "TRANSIENT"
    acquisition_use = SOURCE_RECEIPT_DEFAULT_ACQUISITION_USE
    redistribution_policy = SOURCE_RECEIPT_DEFAULT_REDISTRIBUTION_POLICY

    def __init__(self, spec: ImportSpec) -> None:
        self.spec = spec
        self.payload: SourcePayload | None = None
        self._parsed: ParsedOhlcvRows | None = None

    def load(self, file_path: Path) -> SourcePayload:
        if self.payload is not None:
            return self.payload
        maximum = get_settings().max_csv_bytes
        with file_path.open("rb") as source:
            content = source.read(maximum + 1)
        if not content or len(content) > maximum:
            raise ValueError("CSV must be nonempty and fit the configured byte limit")
        return SourcePayload(content, hashlib.sha256(content).hexdigest(), len(content))

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
        }

    def request_fingerprint_metadata(self, *, source_timezone_name: str) -> dict[str, object]:
        return self.mapping_metadata(source_timezone_name=source_timezone_name)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _utc(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
        raise ValueError("session timestamps must use YYYY-MM-DDTHH:MM:SSZ")
    return datetime.fromisoformat(value).astimezone(UTC)
