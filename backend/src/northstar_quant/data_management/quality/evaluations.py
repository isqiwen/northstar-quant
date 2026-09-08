"""Requests, results, and invariants for append-only quality evaluations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

DAILY_QUALITY_RULE_SET_NAME = "daily_coverage_quality"
DAILY_QUALITY_RULE_SET_VERSION = "1.0.0"
DAILY_QUALITY_EVALUATION_SCOPE = "DAILY_COVERAGE"
MAX_DAILY_QUALITY_WINDOW_DAYS = 366
MINUTE_QUALITY_RULE_SET_NAME = "minute_session_coverage_quality"
MINUTE_QUALITY_RULE_SET_VERSION = "1.0.0"
MINUTE_QUALITY_EVALUATION_SCOPE = "MINUTE_SESSION_COVERAGE"
MAX_MINUTE_QUALITY_WINDOW_DAYS = 31
MAX_MINUTE_QUALITY_SESSION_ROWS = 512
MAX_MINUTE_QUALITY_EXPECTED_SLOTS = 50_000
MAX_MINUTE_QUALITY_OBSERVED_BARS = 50_000
IMPORT_QUALITY_RULE_SET_NAME = "import_integrity_quality"
IMPORT_QUALITY_RULE_SET_VERSION = "2.0.0"
MAX_IMPORT_QUALITY_RECORDS = 250_000
MAX_IMPORT_QUALITY_INSERTED_BARS = 250_000
MAX_IMPORT_QUALITY_PROVIDER_RETRIEVALS = 128
IMPORT_QUALITY_PAGE_SIZE = 500

_OPAQUE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RFC3339_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


class DailyQualityEvaluationError(ValueError):
    """A bounded rejection for one local daily-quality evaluation command."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


class MinuteQualityEvaluationError(ValueError):
    """A bounded rejection for one local minute-quality evaluation."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


class QualityApplicabilityError(ValueError):
    """A bounded rejection while checking whether stored quality evidence still applies."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


class ImportQualityEvaluationError(ValueError):
    """A bounded rejection for one local import-quality evaluation."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


class ImportQualityApplicabilityError(ValueError):
    """A bounded rejection while checking one stored import-quality conclusion."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


@dataclass(frozen=True)
class DailyQualityEvaluationCommand:
    """Evaluate a fixed daily-series window at one explicit cutoff.

    The command is local and side-effect free with respect to source ingestion,
    catalog data, and snapshot publication.  Its only durable result is a new
    append-only quality conclusion and its associated findings.
    """

    series_id: UUID
    from_trading_day: date
    to_trading_day: date
    as_of: datetime
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None


@dataclass(frozen=True)
class DailyQualityEvaluationResult:
    """Safe confirmation payload for the local evaluation command."""

    quality_evaluation_id: UUID
    series_id: UUID
    outcome: str
    delivery_gate: str
    expected_observation_count: int
    covered_observation_count: int
    missing_observation_count: int
    unknown_day_count: int
    finding_count: int
    replayed: bool

    def as_dict(self) -> dict[str, str | int | bool]:
        """Return only identifiers and aggregate conclusions, never source data."""

        return {
            "quality_evaluation_id": str(self.quality_evaluation_id),
            "series_id": str(self.series_id),
            "outcome": self.outcome,
            "delivery_gate": self.delivery_gate,
            "expected_observation_count": self.expected_observation_count,
            "covered_observation_count": self.covered_observation_count,
            "missing_observation_count": self.missing_observation_count,
            "unknown_day_count": self.unknown_day_count,
            "finding_count": self.finding_count,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class MinuteQualityEvaluationCommand:
    """Evaluate complete-session one-minute coverage at one explicit cutoff.

    Freshness means historical ``available_at`` visibility
    at ``as_of``.  It neither invents a source availability SLA nor evaluates a
    live market-data feed.
    """

    series_id: UUID
    from_trading_day: date
    to_trading_day: date
    as_of: datetime
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None


@dataclass(frozen=True)
class MinuteQualityEvaluationResult:
    """Safe aggregate confirmation of immutable minute-session evidence."""

    quality_evaluation_id: UUID
    series_id: UUID
    outcome: str
    delivery_gate: str
    expected_observation_count: int
    covered_observation_count: int
    missing_observation_count: int
    unknown_day_count: int
    finding_count: int
    replayed: bool

    def as_dict(self) -> dict[str, str | int | bool]:
        """Return IDs and aggregate counts only, never canonical values."""

        return {
            "quality_evaluation_id": str(self.quality_evaluation_id),
            "series_id": str(self.series_id),
            "outcome": self.outcome,
            "delivery_gate": self.delivery_gate,
            "expected_observation_count": self.expected_observation_count,
            "covered_observation_count": self.covered_observation_count,
            "missing_observation_count": self.missing_observation_count,
            "unknown_day_count": self.unknown_day_count,
            "finding_count": self.finding_count,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class QualityApplicabilityResult:
    """Read-only applicability of one immutable series-window conclusion."""

    quality_evaluation_id: UUID
    rule_set_name: str
    rule_set_version: str
    evaluation_scope: str
    applicable: bool
    delivery_gate: str
    reason_code: str

    def as_dict(self) -> dict[str, str | bool]:
        """Return only safe identifiers and revalidation state."""

        return {
            "quality_evaluation_id": str(self.quality_evaluation_id),
            "rule_set_name": self.rule_set_name,
            "rule_set_version": self.rule_set_version,
            "evaluation_scope": self.evaluation_scope,
            "applicable": self.applicable,
            "delivery_gate": self.delivery_gate,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class ImportQualityEvaluationCommand:
    """Evaluate one terminal ``ImportRun`` without re-reading its input."""

    import_run_id: UUID
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None


@dataclass(frozen=True)
class ImportQualityEvaluationResult:
    """Safe, aggregate confirmation of an immutable import-quality conclusion."""

    import_quality_evaluation_id: UUID
    import_run_id: UUID
    outcome: str
    delivery_gate: str
    rows_read: int
    rows_accepted: int
    rows_rejected: int
    rows_inserted: int
    rows_duplicate_identical: int
    rows_conflicted: int
    record_count: int
    finding_count: int
    replayed: bool

    def as_dict(self) -> dict[str, str | int | bool]:
        """Return identifiers and aggregate counts only, never source input data."""

        return {
            "import_quality_evaluation_id": str(self.import_quality_evaluation_id),
            "import_run_id": str(self.import_run_id),
            "outcome": self.outcome,
            "delivery_gate": self.delivery_gate,
            "rows_read": self.rows_read,
            "rows_accepted": self.rows_accepted,
            "rows_rejected": self.rows_rejected,
            "rows_inserted": self.rows_inserted,
            "rows_duplicate_identical": self.rows_duplicate_identical,
            "rows_conflicted": self.rows_conflicted,
            "record_count": self.record_count,
            "finding_count": self.finding_count,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class ImportQualityCurrentState:
    """Current, read-only revalidation state for one import-quality protocol.

    This internal application value deliberately carries only the fields that
    an immutable ``ImportQualityEvaluation`` records. Snapshot publication
    can compare it to a named historical conclusion without creating another
    evaluation, changing import evidence, or resolving a different rule revision.
    """

    input_fingerprint: str
    observed_status: str
    observed_effect: str
    outcome: str
    delivery_gate: str
    rows_read: int
    rows_accepted: int
    rows_rejected: int
    rows_inserted: int
    rows_duplicate_identical: int
    rows_conflicted: int
    record_count: int
    finding_count: int


@dataclass(frozen=True)
class ImportQualityApplicabilityResult:
    """Read-only applicability of one immutable import-quality conclusion."""

    import_quality_evaluation_id: UUID
    import_run_id: UUID
    rule_set_name: str
    rule_set_version: str
    applicable: bool
    delivery_gate: str
    reason_code: str

    def as_dict(self) -> dict[str, str | bool]:
        """Return named-evidence state only; never resolve another evaluation."""

        return {
            "import_quality_evaluation_id": str(self.import_quality_evaluation_id),
            "import_run_id": str(self.import_run_id),
            "rule_set_name": self.rule_set_name,
            "rule_set_version": self.rule_set_version,
            "applicable": self.applicable,
            "delivery_gate": self.delivery_gate,
            "reason_code": self.reason_code,
        }


def validate_daily_quality_evaluation_command(
    command: DailyQualityEvaluationCommand,
) -> DailyQualityEvaluationCommand:
    """Normalize all durable command fields before a database transaction starts."""

    if command.from_trading_day > command.to_trading_day:
        raise DailyQualityEvaluationError(
            "INVALID_QUALITY_EVALUATION_RANGE",
            "from_trading_day must not be after to_trading_day",
        )
    if (command.to_trading_day - command.from_trading_day).days + 1 > MAX_DAILY_QUALITY_WINDOW_DAYS:
        raise DailyQualityEvaluationError(
            "QUALITY_EVALUATION_WINDOW_TOO_LARGE",
            f"the daily quality window must contain at most {MAX_DAILY_QUALITY_WINDOW_DAYS} days",
        )
    return DailyQualityEvaluationCommand(
        series_id=command.series_id,
        from_trading_day=command.from_trading_day,
        to_trading_day=command.to_trading_day,
        as_of=require_utc_timestamp(command.as_of, "as_of"),
        idempotency_key=require_opaque_identifier(command.idempotency_key, "idempotency_key"),
        correlation_id=require_opaque_identifier(command.correlation_id, "correlation_id"),
        causation_id=(
            require_opaque_identifier(command.causation_id, "causation_id")
            if command.causation_id is not None
            else None
        ),
    )


def validate_minute_quality_evaluation_command(
    command: MinuteQualityEvaluationCommand,
) -> MinuteQualityEvaluationCommand:
    """Normalize all durable minute-quality command fields before database access."""

    if command.from_trading_day > command.to_trading_day:
        raise MinuteQualityEvaluationError(
            "INVALID_MINUTE_QUALITY_EVALUATION_RANGE",
            "from_trading_day must not be after to_trading_day",
        )
    if (
        command.to_trading_day - command.from_trading_day
    ).days + 1 > MAX_MINUTE_QUALITY_WINDOW_DAYS:
        raise MinuteQualityEvaluationError(
            "MINUTE_QUALITY_EVALUATION_WINDOW_TOO_LARGE",
            f"the minute quality window must contain at most {MAX_MINUTE_QUALITY_WINDOW_DAYS} days",
        )
    return MinuteQualityEvaluationCommand(
        series_id=command.series_id,
        from_trading_day=command.from_trading_day,
        to_trading_day=command.to_trading_day,
        as_of=require_minute_quality_utc_timestamp(command.as_of, "as_of"),
        idempotency_key=require_minute_quality_opaque_identifier(
            command.idempotency_key, "idempotency_key"
        ),
        correlation_id=require_minute_quality_opaque_identifier(
            command.correlation_id, "correlation_id"
        ),
        causation_id=(
            require_minute_quality_opaque_identifier(command.causation_id, "causation_id")
            if command.causation_id is not None
            else None
        ),
    )


def validate_import_quality_evaluation_command(
    command: ImportQualityEvaluationCommand,
) -> ImportQualityEvaluationCommand:
    """Normalize opaque import-quality command identifiers before database access."""

    return ImportQualityEvaluationCommand(
        import_run_id=command.import_run_id,
        idempotency_key=require_import_quality_opaque_identifier(
            command.idempotency_key, "idempotency_key"
        ),
        correlation_id=require_import_quality_opaque_identifier(
            command.correlation_id, "correlation_id"
        ),
        causation_id=(
            require_import_quality_opaque_identifier(command.causation_id, "causation_id")
            if command.causation_id is not None
            else None
        ),
    )


def parse_rfc3339_timestamp(value: str, field_name: str = "as_of") -> datetime:
    """Parse one explicit RFC 3339 cutoff without accepting loose variants."""

    if not _RFC3339_TIMESTAMP_PATTERN.fullmatch(value):
        raise DailyQualityEvaluationError(
            "INVALID_QUALITY_EVALUATION_TIMESTAMP",
            f"{field_name} must be an RFC 3339 timestamp with an explicit UTC offset",
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:  # pragma: no cover - regex catches ordinary malformed input
        raise DailyQualityEvaluationError(
            "INVALID_QUALITY_EVALUATION_TIMESTAMP",
            f"{field_name} must be an RFC 3339 timestamp with an explicit UTC offset",
        ) from error
    return require_utc_timestamp(parsed, field_name)


def parse_minute_quality_rfc3339_timestamp(value: str, field_name: str = "as_of") -> datetime:
    """Parse one strict minute-quality cutoff without sharing daily errors."""

    if not _RFC3339_TIMESTAMP_PATTERN.fullmatch(value):
        raise MinuteQualityEvaluationError(
            "INVALID_MINUTE_QUALITY_EVALUATION_TIMESTAMP",
            f"{field_name} must be an RFC 3339 timestamp with an explicit UTC offset",
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:  # pragma: no cover - regex catches ordinary malformed input
        raise MinuteQualityEvaluationError(
            "INVALID_MINUTE_QUALITY_EVALUATION_TIMESTAMP",
            f"{field_name} must be an RFC 3339 timestamp with an explicit UTC offset",
        ) from error
    return require_minute_quality_utc_timestamp(parsed, field_name)


def require_opaque_identifier(value: str, field_name: str) -> str:
    """Accept a bounded opaque command identifier, never a free-form note."""

    normalized = value.strip()
    if value != normalized or not _OPAQUE_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DailyQualityEvaluationError(
            "INVALID_QUALITY_EVALUATION_IDENTIFIER",
            f"{field_name} must use 1 to 128 letters, digits, '.', '_' or '-'",
        )
    return normalized


def require_minute_quality_opaque_identifier(value: str, field_name: str) -> str:
    """Accept one bounded opaque minute-quality audit identifier."""

    normalized = value.strip()
    if value != normalized or not _OPAQUE_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MinuteQualityEvaluationError(
            "INVALID_MINUTE_QUALITY_EVALUATION_IDENTIFIER",
            f"{field_name} must be a 1-128 character opaque identifier",
        )
    return normalized


def require_minute_quality_utc_timestamp(value: datetime, field_name: str) -> datetime:
    """Normalize an aware minute-quality timestamp to UTC or reject ambiguity."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise MinuteQualityEvaluationError(
            "INVALID_MINUTE_QUALITY_EVALUATION_TIMESTAMP",
            f"{field_name} must include an explicit UTC offset",
        )
    return value.astimezone(UTC)


def require_import_quality_opaque_identifier(value: str, field_name: str) -> str:
    """Apply the shared opaque-ID grammar with an import-quality error type."""

    normalized = value.strip()
    if value != normalized or not _OPAQUE_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ImportQualityEvaluationError(
            "INVALID_IMPORT_QUALITY_IDENTIFIER",
            f"{field_name} must use 1 to 128 letters, digits, '.', '_' or '-'",
        )
    return normalized


def require_utc_timestamp(value: datetime, field_name: str) -> datetime:
    """Require an offset-aware instant and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise DailyQualityEvaluationError(
            "INVALID_QUALITY_EVALUATION_TIMESTAMP",
            f"{field_name} must include an explicit UTC offset",
        )
    return value.astimezone(UTC)
