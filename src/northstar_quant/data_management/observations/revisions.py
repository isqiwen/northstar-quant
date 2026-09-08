"""Requests, identities, and invariants for immutable observation revisions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

OBSERVATION_REVISION_SCHEMA_VERSION = "quant-data-hub/observation-revision/1.0.0"
OBSERVATION_SUPERSESSION_MAPPING_VERSION = "canonical_observation_supersession/1.0.0"
MAX_OBSERVATION_REVISIONS = 64
SUPERSESSION_REASONS = frozenset({"SOURCE_CORRECTION", "PROVIDER_RESTATEMENT"})

_OPAQUE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ObservationRevisionError(ValueError):
    """A bounded fail-closed rejection at the revision Module interface."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]


@dataclass(frozen=True)
class SupersedeObservationCommand:
    """Explicit intent to append one correction after retained conflict evidence."""

    supersedes_observation_id: UUID
    conflict_import_record_id: UUID
    available_at: datetime
    trading_day: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal | None
    open_interest: Decimal | None
    reason: str
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None


@dataclass(frozen=True)
class SupersedeObservationResult:
    """Stable identities produced by an append or exact idempotent replay."""

    observation_id: UUID
    supersedes_observation_id: UUID
    revision_number: int
    import_run_id: UUID
    normalized_payload_hash: str
    replayed: bool


def validate_supersede_observation_command(
    command: SupersedeObservationCommand,
) -> SupersedeObservationCommand:
    """Validate syntax before any authority-store access."""

    if not isinstance(command.supersedes_observation_id, UUID) or not isinstance(
        command.conflict_import_record_id, UUID
    ):
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_ID_INVALID",
            "observation and conflict-import-record identities must be UUIDs",
        )
    if command.available_at.tzinfo is None or command.available_at.utcoffset() is None:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_TIME_AMBIGUOUS",
            "available_at must include an explicit timezone offset",
        )
    reason = command.reason.strip().upper()
    if reason not in SUPERSESSION_REASONS:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_REASON_UNSUPPORTED",
            "reason must be SOURCE_CORRECTION or PROVIDER_RESTATEMENT",
        )
    values = (
        command.open_price,
        command.high_price,
        command.low_price,
        command.close_price,
        command.volume,
        command.turnover,
        command.open_interest,
    )
    if any(value is not None and not value.is_finite() for value in values):
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_DECIMAL_INVALID",
            "observation decimals must be finite exact values",
        )
    return replace(
        command,
        available_at=command.available_at.astimezone(UTC),
        reason=reason,
        idempotency_key=_opaque_identifier(command.idempotency_key, "idempotency_key"),
        correlation_id=_opaque_identifier(command.correlation_id, "correlation_id"),
        causation_id=(
            _opaque_identifier(command.causation_id, "causation_id")
            if command.causation_id is not None
            else None
        ),
    )


def canonical_observation_payload_hash(
    *,
    series_id: UUID | str,
    event_time: datetime,
    trading_day: date,
    available_at: datetime,
    open_price: Decimal,
    high_price: Decimal,
    low_price: Decimal,
    close_price: Decimal,
    volume: Decimal,
    turnover: Decimal | None,
    open_interest: Decimal | None,
) -> str:
    """Hash the exact canonical payload shared by initial import and supersession."""

    if event_time.tzinfo is None or event_time.utcoffset() is None:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_TIME_AMBIGUOUS",
            "event_time must include an explicit timezone offset",
        )
    if available_at.tzinfo is None or available_at.utcoffset() is None:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_TIME_AMBIGUOUS",
            "available_at must include an explicit timezone offset",
        )
    return stable_json_sha256(
        {
            "series_id": str(series_id),
            "event_time": render_utc_timestamp(event_time),
            "trading_day": trading_day.isoformat(),
            "available_at": render_utc_timestamp(available_at),
            "open_price": render_decimal(open_price),
            "high_price": render_decimal(high_price),
            "low_price": render_decimal(low_price),
            "close_price": render_decimal(close_price),
            "volume": render_decimal(volume),
            "turnover": render_decimal(turnover) if turnover is not None else None,
            "open_interest": (render_decimal(open_interest) if open_interest is not None else None),
        }
    )


def stable_json_sha256(value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def render_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_TIME_AMBIGUOUS",
            "timestamps must include an explicit timezone offset",
        )
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def render_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_DECIMAL_INVALID",
            "observation decimals must be finite exact values",
        )
    if value.is_zero():
        value = value.copy_abs()
    return format(value, "f")


def _opaque_identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    if value != normalized or not _OPAQUE_IDENTIFIER.fullmatch(normalized):
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_IDENTIFIER_INVALID",
            f"{field_name} must use 1 to 128 letters, digits, '.', '_' or '-'",
        )
    return normalized
