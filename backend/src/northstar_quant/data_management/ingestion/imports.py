"""Requests, results, and failures for canonical OHLCV imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

AVAILABLE_AT_POLICY = "explicit-source-time-bar-completion-v1"
UNIT_POLICY = "exact-declared-units-no-conversion-v1"

# The Parquet profile has its own identity but maps to the same ``RawOhlcvRow``
# value object so every input format uses one canonical writer.
PARQUET_PROFILE_NAME = "canonical_ohlcv_parquet"
PARQUET_PROFILE_VERSION = "1.0.0"
PARQUET_MAPPING_VERSION = f"{PARQUET_PROFILE_NAME}/{PARQUET_PROFILE_VERSION}"
PARQUET_AVAILABLE_AT_POLICY = AVAILABLE_AT_POLICY
PARQUET_UNIT_POLICY = UNIT_POLICY


@dataclass(frozen=True)
class CanonicalRejectionRecord:
    """Per-row evidence when a fully normalized batch is quarantined."""

    source_row_number: int
    source_record_id: str
    normalized_payload_hash: str
    event_time: datetime
    disposition: str
    error_code: str
    evidence: str


class OhlcvImportError(ValueError):
    """A bounded, operator-safe rejection for one import command."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        rows_read: int = 0,
        rows_rejected: int = 0,
        quarantined: bool = False,
        canonical_rejection_records: tuple[CanonicalRejectionRecord, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]
        self.rows_read = rows_read
        self.rows_rejected = rows_rejected
        self.quarantined = quarantined
        self.canonical_rejection_records = canonical_rejection_records


class IdempotencyKeyReuseError(OhlcvImportError):
    """Raised when an opaque idempotency key is reused for a different intent."""


class ImportInProgressError(OhlcvImportError):
    """Raised when another transaction still owns an equivalent import attempt."""


@dataclass(frozen=True)
class OhlcvImportCommand:
    """An operator-supplied, single-series canonical input command."""

    file_path: Path
    series_id: UUID
    source_name: str
    source_timezone_name: str
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None


@dataclass(frozen=True)
class SourcePayload:
    """Bounded transient source bytes and their receipt identity."""

    content: bytes
    content_hash: str
    byte_count: int


@dataclass(frozen=True)
class RawOhlcvRow:
    """One syntactically valid row before catalog-specific normalization."""

    source_row_number: int
    symbol: str
    interval: str
    event_time: datetime | None
    trading_day: date
    available_at: datetime
    source_record_id: str
    price_currency: str
    volume_unit: str
    open_interest_unit: str
    turnover_currency: str
    turnover_multiplier: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal | None
    open_interest: Decimal | None


@dataclass(frozen=True)
class ParsedOhlcvRows:
    """A parsed input plus the mapping/profile metadata that governed it."""

    rows: tuple[RawOhlcvRow, ...]
    mapping: dict[str, object]


@dataclass(frozen=True)
class NormalizedBar:
    """A canonical candidate ready for conflict comparison and persistence."""

    source_row_number: int
    source_record_id: str
    event_time: datetime
    trading_day: date
    available_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal | None
    open_interest: Decimal | None
    normalized_payload_hash: str


@dataclass(frozen=True)
class OhlcvImportResult:
    """A compact result returned by the CLI and its application-service caller."""

    import_run_id: UUID
    job_run_id: UUID | None
    source_receipt_id: UUID | None
    status: str
    effect: str
    rows_read: int
    rows_accepted: int
    rows_rejected: int
    rows_inserted: int
    rows_duplicate_identical: int
    rows_conflicted: int
    replayed: bool

    def as_dict(self) -> dict[str, str | int | bool | None]:
        """Return stable JSON-safe output without exposing local input paths."""

        return {
            "import_run_id": str(self.import_run_id),
            "job_run_id": str(self.job_run_id) if self.job_run_id is not None else None,
            "source_receipt_id": (
                str(self.source_receipt_id) if self.source_receipt_id is not None else None
            ),
            "status": self.status,
            "effect": self.effect,
            "rows_read": self.rows_read,
            "rows_accepted": self.rows_accepted,
            "rows_rejected": self.rows_rejected,
            "rows_inserted": self.rows_inserted,
            "rows_duplicate_identical": self.rows_duplicate_identical,
            "rows_conflicted": self.rows_conflicted,
            "replayed": self.replayed,
        }
