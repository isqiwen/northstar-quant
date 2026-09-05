"""Requests, results, and invariants for snapshot publication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

SNAPSHOT_MANIFEST_SCHEMA_VERSION = "2.0.0"
SNAPSHOT_DATASET_KIND = "FUTURES_OHLCV"
SNAPSHOT_CANONICAL_SCHEMA_VERSION = "canonical_ohlcv/1.0.0"
MAX_SNAPSHOT_PARTITIONS = 32
MAX_SNAPSHOT_MEMBERS = 250_000
MAX_SNAPSHOT_REVISION_ROWS = 500_000
MAX_SNAPSHOT_IMPORT_QUALITY_PINS = 512

_OPAQUE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class DatasetSnapshotPublicationError(ValueError):
    """A bounded, operator-safe rejection for immutable snapshot publication."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


class DatasetSnapshotResolutionError(ValueError):
    """A fail-closed error while resolving a published logical snapshot."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


@dataclass(frozen=True)
class SnapshotPartitionSelection:
    """One explicit inclusive trading-day selection for a single DataSeries."""

    series_id: UUID
    from_trading_day: date
    to_trading_day: date
    quality_evaluation_id: UUID


@dataclass(frozen=True)
class SnapshotPartitionMetadata:
    """Frozen interpretation metadata for one logical snapshot partition.

    The mutable catalog remains the authority for creating a new snapshot, but
    a published snapshot must carry every field a reader needs to interpret its
    canonical OHLCV rows.  Future read/export adapters therefore consume this
    stored shape rather than reloading live ``DataSeries`` or calendar fields.
    """

    series_id: UUID
    contract_id: UUID
    contract_code: str
    product_code: str
    exchange_code: str
    exchange_timezone_name: str
    calendar_id: UUID
    calendar_code: str
    calendar_revision: int
    calendar_timezone_name: str
    series_kind: str
    interval: str
    adjustment: str
    timestamp_convention: str
    price_scale: int
    quantity_scale: int
    price_currency: str
    price_tick: Decimal
    contract_multiplier: Decimal
    quantity_unit: str
    volume_unit: str
    turnover_currency: str


@dataclass(frozen=True)
class SnapshotImportQualityPinSelection:
    """One exact import-run to import-quality-evaluation mapping."""

    import_run_id: UUID
    import_quality_evaluation_id: UUID


@dataclass(frozen=True)
class PublishDatasetSnapshotCommand:
    """Explicit intent for one atomic, immutable logical snapshot.

    This command intentionally contains no ``latest`` selectors, paths, object
    storage targets, row IDs, or mutation controls. The Hub derives membership
    from the bounded series ranges inside one authoritative transaction.
    """

    available_at_cutoff: datetime
    partitions: tuple[SnapshotPartitionSelection, ...]
    import_quality_pins: tuple[SnapshotImportQualityPinSelection, ...]
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None


@dataclass(frozen=True)
class DatasetSnapshotPublicationResult:
    """Compact confirmation of one publication or exact idempotent replay."""

    snapshot_id: UUID
    manifest_schema_version: str
    content_hash: str
    available_at_cutoff: datetime
    partition_count: int
    member_count: int
    import_quality_pin_count: int
    replayed: bool

    def as_dict(self) -> dict[str, str | int | bool]:
        return {
            "snapshot_id": str(self.snapshot_id),
            "manifest_schema_version": self.manifest_schema_version,
            "dataset_kind": SNAPSHOT_DATASET_KIND,
            "content_hash": self.content_hash,
            "available_at_cutoff": _render_timestamp(self.available_at_cutoff),
            "partition_count": self.partition_count,
            "member_count": self.member_count,
            "import_quality_pin_count": self.import_quality_pin_count,
            "replayed": self.replayed,
        }


def validate_publish_dataset_snapshot_command(
    command: PublishDatasetSnapshotCommand,
) -> PublishDatasetSnapshotCommand:
    """Normalize and bound all durable publication intent before database access."""

    cutoff = _require_utc_timestamp(command.available_at_cutoff, "available_at_cutoff")
    if not command.partitions:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITIONS_REQUIRED",
            "at least one explicit series partition is required",
        )
    if len(command.partitions) > MAX_SNAPSHOT_PARTITIONS:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_LIMIT_EXCEEDED",
            f"a snapshot may contain at most {MAX_SNAPSHOT_PARTITIONS} series partitions",
        )
    if len(command.import_quality_pins) > MAX_SNAPSHOT_IMPORT_QUALITY_PINS:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_IMPORT_PIN_LIMIT_EXCEEDED",
            "a snapshot may contain at most "
            f"{MAX_SNAPSHOT_IMPORT_QUALITY_PINS} import-quality pins",
        )

    normalized_partitions = tuple(
        _normalize_partition_selection(item) for item in command.partitions
    )
    normalized_pins = tuple(
        _normalize_import_pin_selection(item) for item in command.import_quality_pins
    )

    if len({item.series_id for item in normalized_partitions}) != len(normalized_partitions):
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_SERIES_SELECTION_DUPLICATE",
            "a snapshot may select a DataSeries only once",
        )
    if len({item.import_run_id for item in normalized_pins}) != len(normalized_pins):
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_IMPORT_RUN_PIN_DUPLICATE",
            "an import run may be pinned only once",
        )
    if len({item.import_quality_evaluation_id for item in normalized_pins}) != len(normalized_pins):
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_IMPORT_EVALUATION_PIN_DUPLICATE",
            "an import-quality evaluation may be pinned only once",
        )

    return PublishDatasetSnapshotCommand(
        available_at_cutoff=cutoff,
        partitions=normalized_partitions,
        import_quality_pins=normalized_pins,
        idempotency_key=require_snapshot_opaque_identifier(
            command.idempotency_key, "idempotency_key"
        ),
        correlation_id=require_snapshot_opaque_identifier(command.correlation_id, "correlation_id"),
        causation_id=(
            require_snapshot_opaque_identifier(command.causation_id, "causation_id")
            if command.causation_id is not None
            else None
        ),
    )


def _normalize_partition_selection(
    selection: SnapshotPartitionSelection,
) -> SnapshotPartitionSelection:
    if selection.from_trading_day > selection.to_trading_day:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_RANGE_INVALID",
            "from_trading_day must not be after to_trading_day",
        )
    return selection


def _normalize_import_pin_selection(
    selection: SnapshotImportQualityPinSelection,
) -> SnapshotImportQualityPinSelection:
    return selection


def require_snapshot_opaque_identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    if value != normalized or not _OPAQUE_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_IDENTIFIER_INVALID",
            f"{field_name} must use 1 to 128 letters, digits, '.', '_' or '-'",
        )
    return normalized


def _require_utc_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_TIMESTAMP_INVALID",
            f"{field_name} must include an explicit UTC offset",
        )
    return value.astimezone(UTC)


def _render_timestamp(value: datetime) -> str:
    normalized = _require_utc_timestamp(value, "timestamp")
    return normalized.isoformat().replace("+00:00", "Z")
