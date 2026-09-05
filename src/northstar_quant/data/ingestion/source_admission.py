"""Commands and invariants for SHFE daily source-admission evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

# The catalog model owns these physical values and constraints. Re-export them
# here so admission and retrieval code share one set of literals.
from northstar_quant.data.catalog.models import (
    SHFE_DAILY_ACQUISITION_USE,
    SHFE_DAILY_ADAPTER_NAME,
    SHFE_DAILY_ADAPTER_VERSION,
    SHFE_DAILY_AVAILABLE_AT_BASIS,
    SHFE_DAILY_ENDPOINT_ID,
    SHFE_DAILY_MAPPING_VERSION,
    SHFE_DAILY_REDISTRIBUTION_POLICY,
    SHFE_DAILY_RETENTION_POLICY,
    SHFE_DAILY_SOURCE_ADMISSION_REVIEW_STATUSES,
    SHFE_DAILY_SOURCE_NAME,
)

__all__ = [
    "RecordShfeDailySourceAdmissionReviewCommand",
    "SHFE_DAILY_ACQUISITION_USE",
    "SHFE_DAILY_ADAPTER_NAME",
    "SHFE_DAILY_ADAPTER_VERSION",
    "SHFE_DAILY_AVAILABLE_AT_BASIS",
    "SHFE_DAILY_ENDPOINT_ID",
    "SHFE_DAILY_MAPPING_VERSION",
    "SHFE_DAILY_REDISTRIBUTION_POLICY",
    "SHFE_DAILY_RETENTION_POLICY",
    "SHFE_DAILY_SOURCE_ADMISSION_REVIEW_STATUSES",
    "SHFE_DAILY_SOURCE_NAME",
    "ShfeDailySourceAdmissionReviewResult",
    "SourceAdmissionReviewError",
    "parse_rfc3339_timestamp",
    "require_opaque_identifier",
    "require_sha256",
    "require_utc_timestamp",
    "validate_record_shfe_daily_source_admission_review_command",
]

_OPAQUE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_RFC3339_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


class SourceAdmissionReviewError(ValueError):
    """A safe, bounded rejection for one source-admission review command."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


@dataclass(frozen=True)
class RecordShfeDailySourceAdmissionReviewCommand:
    """A local, operator-accountable SHFE daily source-admission conclusion.

    The evidence material itself remains in an access-controlled external system.
    This command stores only a constrained reference and its SHA-256 digest; it
    never accepts a URL, header, path, credential, or free-form note.
    """

    status: str
    evidence_ref: str
    evidence_sha256: str
    reviewer_id: str
    valid_until: datetime
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None


@dataclass(frozen=True)
class ShfeDailySourceAdmissionReviewResult:
    """Minimal JSON-safe confirmation for the local review-record command."""

    source_admission_review_id: UUID
    status: str
    valid_until: datetime
    replayed: bool

    def as_dict(self) -> dict[str, str | bool]:
        """Return no source reference, hash, reviewer identity, or request metadata."""

        return {
            "source_admission_review_id": str(self.source_admission_review_id),
            "status": self.status,
            "valid_until": _as_utc(self.valid_until).isoformat(),
            "replayed": self.replayed,
        }


def validate_record_shfe_daily_source_admission_review_command(
    command: RecordShfeDailySourceAdmissionReviewCommand,
) -> RecordShfeDailySourceAdmissionReviewCommand:
    """Validate durable review evidence before any database transaction starts."""

    if command.status not in SHFE_DAILY_SOURCE_ADMISSION_REVIEW_STATUSES:
        raise SourceAdmissionReviewError(
            "INVALID_SOURCE_ADMISSION_REVIEW_STATUS",
            "status must be one of APPROVED, RESTRICTED, BLOCKED, or UNKNOWN",
        )
    evidence_ref = require_opaque_identifier(command.evidence_ref, "evidence_ref")
    reviewer_id = require_opaque_identifier(command.reviewer_id, "reviewer_id")
    idempotency_key = require_opaque_identifier(command.idempotency_key, "idempotency_key")
    correlation_id = require_opaque_identifier(command.correlation_id, "correlation_id")
    causation_id = (
        require_opaque_identifier(command.causation_id, "causation_id")
        if command.causation_id is not None
        else None
    )
    evidence_sha256 = require_sha256(command.evidence_sha256)
    valid_until = require_utc_timestamp(command.valid_until, "valid_until")
    return RecordShfeDailySourceAdmissionReviewCommand(
        status=command.status,
        evidence_ref=evidence_ref,
        evidence_sha256=evidence_sha256,
        reviewer_id=reviewer_id,
        valid_until=valid_until,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def parse_rfc3339_timestamp(value: str, field_name: str = "valid_until") -> datetime:
    """Parse an explicit RFC 3339 instant without accepting whitespace variants."""

    if not _RFC3339_TIMESTAMP_PATTERN.fullmatch(value):
        raise SourceAdmissionReviewError(
            "INVALID_SOURCE_ADMISSION_REVIEW_TIMESTAMP",
            f"{field_name} must be an RFC 3339 timestamp with an explicit UTC offset",
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:  # pragma: no cover - regex catches normal malformed inputs
        raise SourceAdmissionReviewError(
            "INVALID_SOURCE_ADMISSION_REVIEW_TIMESTAMP",
            f"{field_name} must be an RFC 3339 timestamp with an explicit UTC offset",
        ) from error
    return require_utc_timestamp(parsed, field_name)


def require_opaque_identifier(value: str, field_name: str) -> str:
    """Accept one durable opaque identifier, never note-like source text."""

    normalized = value.strip()
    if value != normalized or not _OPAQUE_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise SourceAdmissionReviewError(
            "INVALID_SOURCE_ADMISSION_REVIEW_IDENTIFIER",
            f"{field_name} must use 1 to 128 letters, digits, '.', '_' or '-'",
        )
    return normalized


def require_sha256(value: str) -> str:
    """Accept exactly one lower-case SHA-256 digest with no prefix or whitespace."""

    if not _SHA256_PATTERN.fullmatch(value):
        raise SourceAdmissionReviewError(
            "INVALID_SOURCE_ADMISSION_REVIEW_EVIDENCE_SHA256",
            "evidence_sha256 must be exactly 64 lower-case hexadecimal characters",
        )
    return value


def require_utc_timestamp(value: datetime, field_name: str) -> datetime:
    """Normalize a concrete offset-aware timestamp to UTC for durable comparison."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceAdmissionReviewError(
            "INVALID_SOURCE_ADMISSION_REVIEW_TIMESTAMP",
            f"{field_name} must be an RFC 3339 timestamp with an explicit UTC offset",
        )
    return value.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize one explicit instant to UTC."""

    return require_utc_timestamp(value, "timestamp")
