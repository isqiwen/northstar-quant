"""Transactional OHLCV import orchestration and canonical normalization."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from itertools import islice
from pathlib import Path
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from northstar_quant.data.catalog.models import (
    SOURCE_RECEIPT_ACQUISITION_USES,
    SOURCE_RECEIPT_REDISTRIBUTION_POLICIES,
    CalendarTradingDay,
    CanonicalBar,
    DataSeries,
    FuturesContract,
    ImportRecord,
    ImportRun,
    JobRun,
    SourceReceipt,
    TradingSession,
)
from northstar_quant.data.ingestion.imports import (
    AVAILABLE_AT_POLICY,
    UNIT_POLICY,
    CanonicalRejectionRecord,
    IdempotencyKeyReuseError,
    ImportInProgressError,
    NormalizedBar,
    OhlcvImportCommand,
    OhlcvImportError,
    OhlcvImportResult,
    ParsedOhlcvRows,
    RawOhlcvRow,
    SourcePayload,
)
from northstar_quant.data.observations.revisions import canonical_observation_payload_hash

_SOURCE_NAME_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]*")
_BATCH_SIZE = 500
_PRICE_PRECISION = 24
_QUANTITY_PRECISION = 28
_TURNOVER_PRECISION = 32
_DATABASE_SCALE = 12


class CanonicalOhlcvInputAdapter(Protocol):
    """Bounded source decoder feeding the shared canonical-write path."""

    media_type: str
    mapping_version: str
    job_kind: str
    input_kind: str
    retention_policy: str
    acquisition_use: str
    redistribution_policy: str

    def load(self, file_path: Path) -> SourcePayload: ...

    def parse(self, payload: SourcePayload, *, source_timezone_name: str) -> ParsedOhlcvRows: ...

    def mapping_metadata(self, *, source_timezone_name: str) -> dict[str, object]: ...

    def request_fingerprint_metadata(self, *, source_timezone_name: str) -> dict[str, object]: ...


class OhlcvImportService:
    """Import one bounded canonical-OHLCV input without a public mutation endpoint.

    The service owns its database transaction: callers receive either a durable
    terminal result or a retryable exception.  It does not publish a dataset
    snapshot; quality evaluation and snapshot publication are separate actions.
    """

    def __init__(
        self,
        session: Session,
        *,
        adapter: CanonicalOhlcvInputAdapter,
        mutation_guard: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._adapter = adapter
        self._mutation_guard = mutation_guard

    def import_file(self, command: OhlcvImportCommand) -> OhlcvImportResult:
        """Execute one idempotent local source-import command."""

        command = _validate_command(command)
        _validate_adapter_source_policy(self._adapter)
        payload = self._adapter.load(command.file_path)
        request_semantics = self._adapter.request_fingerprint_metadata(
            source_timezone_name=command.source_timezone_name
        )
        request_fingerprint = _request_fingerprint(
            command,
            payload.content_hash,
            mapping_version=self._adapter.mapping_version,
            request_semantics=request_semantics or None,
        )
        existing = self._find_existing(command, request_fingerprint)
        if existing is not None:
            self._session.rollback()
            return _result_from_import_run(existing, replayed=True, effect="NOOP")

        mapping = self._adapter.mapping_metadata(source_timezone_name=command.source_timezone_name)
        try:
            parsed = self._adapter.parse(payload, source_timezone_name=command.source_timezone_name)
            series = self._load_active_series(command.series_id, rows_read=len(parsed.rows))
            mapping = _enrich_mapping(mapping, series)
            normalized = self._normalize(parsed.rows, series, command.source_timezone_name)
        except OhlcvImportError as error:
            self._session.rollback()
            return self._record_terminal_error(
                command=command,
                payload=payload,
                request_fingerprint=request_fingerprint,
                mapping=mapping,
                error=error,
            )

        # Parsing/normalization intentionally happen outside the short write
        # transaction.  Release any read transaction before taking the series lock.
        self._session.rollback()
        try:
            return self._apply_normalized_import(
                command=command,
                payload=payload,
                request_fingerprint=request_fingerprint,
                mapping=mapping,
                normalized=normalized,
            )
        except OhlcvImportError as error:
            self._session.rollback()
            return self._record_terminal_error(
                command=command,
                payload=payload,
                request_fingerprint=request_fingerprint,
                mapping=mapping,
                error=error,
            )
        except IntegrityError as failure:
            self._session.rollback()
            existing = self._find_existing(command, request_fingerprint)
            if existing is not None:
                self._session.rollback()
                return _result_from_import_run(existing, replayed=True, effect="NOOP")
            return self._record_terminal_error(
                command=command,
                payload=payload,
                request_fingerprint=request_fingerprint,
                mapping=mapping,
                error=OhlcvImportError(
                    "CANONICAL_BAR_CONCURRENT_CONFLICT"
                    if getattr(failure.orig, "sqlstate", None) == "23505"
                    else "IMPORT_DATA_INVARIANT_FAILED",
                    "a concurrent import changed this series; retry the same input safely"
                    if getattr(failure.orig, "sqlstate", None) == "23505"
                    else "normalized input did not satisfy persisted data invariants",
                    rows_read=len(normalized),
                    rows_rejected=len(normalized),
                    quarantined=True,
                ),
            )

    def _find_existing(
        self, command: OhlcvImportCommand, request_fingerprint: str
    ) -> ImportRun | None:
        existing_job = self._session.scalar(
            select(JobRun)
            .where(
                JobRun.job_kind == self._adapter.job_kind,
                JobRun.idempotency_key == command.idempotency_key,
            )
            .options(joinedload(JobRun.import_run))
        )
        if existing_job is not None:
            existing_import = existing_job.import_run
            if existing_import is None:
                raise ImportInProgressError(
                    "IDEMPOTENCY_KEY_UNAVAILABLE",
                    "the idempotency key is reserved by an incomplete import job",
                )
            if existing_import.request_fingerprint != request_fingerprint:
                raise IdempotencyKeyReuseError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "the idempotency key was already used for a different import intent",
                )
            if existing_import.status in {"PENDING", "RUNNING"}:
                raise ImportInProgressError(
                    "IMPORT_IN_PROGRESS",
                    "an equivalent import is still in progress",
                )
            return existing_import

        existing_content = self._session.scalar(
            select(ImportRun)
            .where(
                ImportRun.request_fingerprint == request_fingerprint,
                ImportRun.source_receipt_id.is_not(None),
            )
            .options(joinedload(ImportRun.job_run))
        )
        if existing_content is not None:
            if existing_content.status in {"PENDING", "RUNNING"}:
                raise ImportInProgressError(
                    "IMPORT_IN_PROGRESS",
                    "an equivalent source receipt is still being imported",
                )
            return existing_content
        return None

    def _load_active_series(self, series_id: UUID, *, rows_read: int) -> DataSeries:
        statement: Select[tuple[DataSeries]] = (
            select(DataSeries)
            .where(DataSeries.id == series_id)
            .options(
                joinedload(DataSeries.contract).joinedload(FuturesContract.product),
                joinedload(DataSeries.calendar),
            )
        )
        series = self._session.scalar(statement)
        if series is None:
            raise OhlcvImportError(
                "UNKNOWN_SERIES",
                "the requested data series is not cataloged",
                rows_read=rows_read,
                rows_rejected=rows_read,
            )
        if series.status != "ACTIVE":
            raise OhlcvImportError(
                "SERIES_NOT_ACTIVE",
                "the requested data series is not active for ingestion",
                rows_read=rows_read,
                rows_rejected=rows_read,
            )
        if series.volume_unit is None or series.turnover_currency is None:
            raise OhlcvImportError(
                "SERIES_UNIT_CONFIGURATION_MISSING",
                "the requested data series lacks volume/turnover unit metadata",
                rows_read=rows_read,
                rows_rejected=rows_read,
            )
        return series

    def _normalize(
        self,
        rows: Sequence[RawOhlcvRow],
        series: DataSeries,
        source_timezone_name: str,
    ) -> tuple[NormalizedBar, ...]:
        assert series.contract is not None
        assert series.contract.product is not None
        assert series.calendar is not None
        source_timezone = _load_timezone(source_timezone_name)
        _load_timezone(series.calendar.timezone_name)
        sessions = self._load_session_windows(series.calendar_id)
        sessions_by_day = _group_sessions_by_day(sessions)
        open_days = self._load_open_days(series.calendar_id, {row.trading_day for row in rows})
        normalized: list[NormalizedBar] = []

        for raw in rows:
            _validate_source_timezone(raw.available_at, source_timezone, raw, "available_at")
            _validate_row_identity(raw, series, rows_read=len(rows))
            if raw.trading_day not in open_days:
                raise _row_error(
                    "UNKNOWN_OR_CLOSED_TRADING_DAY",
                    raw,
                    "trading_day is not an explicit OPEN day in the pinned calendar",
                )
            day_sessions = sessions_by_day.get(raw.trading_day, ())
            if not day_sessions:
                raise _row_error(
                    "MISSING_TRADING_SESSION",
                    raw,
                    "trading_day has no materialized session in the pinned calendar",
                )

            event_time = self._resolve_event_time(
                raw=raw,
                series=series,
                sessions=day_sessions,
                source_timezone=source_timezone,
            )
            if raw.available_at < _bar_completion_time(series.interval, event_time, day_sessions):
                raise _row_error(
                    "AVAILABLE_BEFORE_BAR_COMPLETE",
                    raw,
                    "available_at precedes the completion of this BAR_START observation",
                )

            values = _validate_numeric_values(raw, series)
            normalized.append(
                NormalizedBar(
                    source_row_number=raw.source_row_number,
                    source_record_id=raw.source_record_id,
                    event_time=event_time,
                    trading_day=raw.trading_day,
                    available_at=raw.available_at,
                    open_price=values.open_price,
                    high_price=values.high_price,
                    low_price=values.low_price,
                    close_price=values.close_price,
                    volume=values.volume,
                    turnover=values.turnover,
                    open_interest=values.open_interest,
                    normalized_payload_hash=_normalized_payload_hash(
                        series_id=str(series.id),
                        event_time=event_time,
                        trading_day=raw.trading_day,
                        available_at=raw.available_at,
                        open_price=values.open_price,
                        high_price=values.high_price,
                        low_price=values.low_price,
                        close_price=values.close_price,
                        volume=values.volume,
                        turnover=values.turnover,
                        open_interest=values.open_interest,
                    ),
                )
            )
        duplicate_event_times = {
            event_time
            for event_time, count in Counter(bar.event_time for bar in normalized).items()
            if count > 1
        }
        if duplicate_event_times:
            rejection_records = tuple(
                CanonicalRejectionRecord(
                    source_row_number=bar.source_row_number,
                    source_record_id=bar.source_record_id,
                    normalized_payload_hash=bar.normalized_payload_hash,
                    event_time=bar.event_time,
                    disposition=(
                        "CONFLICT" if bar.event_time in duplicate_event_times else "REJECTED"
                    ),
                    error_code=(
                        "DUPLICATE_CANONICAL_KEY"
                        if bar.event_time in duplicate_event_times
                        else "BATCH_ABORTED_BY_CONFLICT"
                    ),
                    evidence=(
                        "another input row maps to the same canonical key"
                        if bar.event_time in duplicate_event_times
                        else "batch was not partially committed after a canonical conflict"
                    ),
                )
                for bar in normalized
            )
            raise OhlcvImportError(
                "DUPLICATE_CANONICAL_KEY",
                "multiple input rows map to the same canonical bar",
                rows_read=len(rows),
                rows_rejected=len(rows),
                quarantined=True,
                canonical_rejection_records=rejection_records,
            )
        return tuple(normalized)

    def _resolve_event_time(
        self,
        *,
        raw: RawOhlcvRow,
        series: DataSeries,
        sessions: Sequence[_SessionWindow],
        source_timezone: ZoneInfo,
    ) -> datetime:
        if series.interval == "1m":
            if raw.event_time is None:
                raise _row_error(
                    "MISSING_EVENT_TIME",
                    raw,
                    "event_time is required for a one-minute BAR_START series",
                )
            _validate_source_timezone(raw.event_time, source_timezone, raw, "event_time")
            if raw.event_time.second != 0 or raw.event_time.microsecond != 0:
                raise _row_error(
                    "MINUTE_BOUNDARY_REQUIRED",
                    raw,
                    "one-minute event_time must fall exactly on a minute boundary",
                )
            matching = _containing_session(raw.event_time, sessions)
            if matching is None or matching.trading_day != raw.trading_day:
                raise _row_error(
                    "EVENT_TIME_OUTSIDE_SESSION",
                    raw,
                    "event_time does not map to the declared trading_day/session",
                )
            if raw.event_time + timedelta(minutes=1) > matching.closes_at:
                raise _row_error(
                    "BAR_CROSSES_SESSION_CLOSE",
                    raw,
                    "one-minute bar crosses the end of its materialized session",
                )
            return raw.event_time

        if series.interval == "1d":
            first_open = min(session.opens_at for session in sessions)
            if raw.event_time is not None:
                _validate_source_timezone(raw.event_time, source_timezone, raw, "event_time")
                if raw.event_time != first_open:
                    raise _row_error(
                        "DAILY_EVENT_TIME_NOT_BAR_START",
                        raw,
                        "daily event_time must equal the first pinned session opening or be blank",
                    )
            return first_open

        raise _row_error("UNSUPPORTED_SERIES_INTERVAL", raw, "series interval is unsupported")

    def _load_session_windows(self, calendar_id: UUID) -> tuple[_SessionWindow, ...]:
        sessions = self._session.scalars(
            select(TradingSession)
            .where(TradingSession.calendar_id == calendar_id)
            .order_by(TradingSession.opens_at, TradingSession.sequence)
        ).all()
        return tuple(
            _SessionWindow(
                trading_day=session.trading_day,
                opens_at=_calendar_datetime(session.opens_at),
                closes_at=_calendar_datetime(session.closes_at),
            )
            for session in sessions
        )

    def _load_open_days(self, calendar_id: UUID, trading_days: set[date]) -> set[date]:
        if not trading_days:
            return set()
        rows = self._session.scalars(
            select(CalendarTradingDay).where(
                CalendarTradingDay.calendar_id == calendar_id,
                CalendarTradingDay.trading_day.in_(trading_days),
                CalendarTradingDay.status == "OPEN",
            )
        ).all()
        return {row.trading_day for row in rows}

    def _record_terminal_error(
        self,
        *,
        command: OhlcvImportCommand,
        payload: SourcePayload,
        request_fingerprint: str,
        mapping: dict[str, object],
        error: OhlcvImportError,
    ) -> OhlcvImportResult:
        existing = self._find_existing(command, request_fingerprint)
        if existing is not None:
            self._session.rollback()
            return _result_from_import_run(existing, replayed=True, effect="NOOP")
        try:
            self._assert_mutation_permitted()
            receipt = self._ensure_receipt(command, payload)
            series = self._session.get(DataSeries, command.series_id)
            job, import_run = self._create_attempt(
                command=command,
                receipt=receipt,
                series=series,
                request_fingerprint=request_fingerprint,
                mapping=mapping,
            )
            for record in error.canonical_rejection_records:
                self._session.add(
                    ImportRecord(
                        import_run_id=import_run.id,
                        source_row_number=record.source_row_number,
                        source_record_id=record.source_record_id,
                        normalized_payload_hash=record.normalized_payload_hash,
                        event_time=record.event_time,
                        disposition=record.disposition,
                        error_code=record.error_code,
                        evidence=record.evidence,
                    )
                )
            now = _utc_now()
            import_run.status = "QUARANTINED" if error.quarantined else "FAILED"
            import_run.effect = "REJECTED"
            import_run.rows_read = error.rows_read
            import_run.rows_rejected = error.rows_rejected or 1
            import_run.rows_accepted = 0
            import_run.rows_inserted = 0
            import_run.rows_duplicate_identical = 0
            import_run.rows_conflicted = sum(
                record.disposition == "CONFLICT" for record in error.canonical_rejection_records
            )
            if error.code == "CANONICAL_BAR_CONCURRENT_CONFLICT":
                import_run.rows_conflicted = error.rows_rejected
            import_run.error_code = error.code
            import_run.error_detail = error.detail
            import_run.finished_at = now
            job.status = "FAILED"
            job.error_code = error.code
            job.finished_at = now
            self._session.commit()
            return _result_from_import_run(import_run, replayed=False)
        except IntegrityError:
            self._session.rollback()
            existing = self._find_existing(command, request_fingerprint)
            if existing is not None:
                self._session.rollback()
                return _result_from_import_run(existing, replayed=True, effect="NOOP")
            raise

    def _apply_normalized_import(
        self,
        *,
        command: OhlcvImportCommand,
        payload: SourcePayload,
        request_fingerprint: str,
        mapping: dict[str, object],
        normalized: Sequence[NormalizedBar],
    ) -> OhlcvImportResult:
        self._assert_mutation_permitted()
        receipt = self._ensure_receipt(command, payload)
        series = self._lock_active_series(command.series_id, rows_read=len(normalized))
        job, import_run = self._create_attempt(
            command=command,
            receipt=receipt,
            series=series,
            request_fingerprint=request_fingerprint,
            mapping=mapping,
        )
        existing_by_event_time = self._existing_bars(
            series.id, (bar.event_time for bar in normalized)
        )
        conflicts = {
            candidate.event_time: existing
            for candidate in normalized
            if (existing := existing_by_event_time.get(candidate.event_time)) is not None
            and not _canonical_payload_matches(existing, candidate)
        }

        if conflicts:
            self._record_conflicted_batch(import_run, normalized, existing_by_event_time, conflicts)
            now = _utc_now()
            import_run.status = "QUARANTINED"
            import_run.effect = "REJECTED"
            import_run.rows_read = len(normalized)
            import_run.rows_accepted = 0
            import_run.rows_rejected = len(normalized)
            import_run.rows_inserted = 0
            import_run.rows_duplicate_identical = 0
            import_run.rows_conflicted = len(conflicts)
            import_run.error_code = "CANONICAL_BAR_CONFLICT"
            import_run.error_detail = "the input conflicts with an existing canonical observation"
            import_run.finished_at = now
            job.status = "FAILED"
            job.error_code = "CANONICAL_BAR_CONFLICT"
            job.finished_at = now
            self._session.commit()
            return _result_from_import_run(import_run, replayed=False)

        inserted_by_event_time: dict[datetime, CanonicalBar] = {}
        for candidate in normalized:
            if candidate.event_time in existing_by_event_time:
                continue
            bar = CanonicalBar(
                series_id=series.id,
                import_run_id=import_run.id,
                event_time=candidate.event_time,
                trading_day=candidate.trading_day,
                available_at=candidate.available_at,
                source_timezone_name=command.source_timezone_name,
                source_name=command.source_name,
                source_record_id=candidate.source_record_id,
                source_content_hash=payload.content_hash,
                normalized_payload_hash=candidate.normalized_payload_hash,
                open_price=candidate.open_price,
                high_price=candidate.high_price,
                low_price=candidate.low_price,
                close_price=candidate.close_price,
                volume=candidate.volume,
                turnover=candidate.turnover,
                open_interest=candidate.open_interest,
            )
            self._session.add(bar)
            inserted_by_event_time[candidate.event_time] = bar
        self._session.flush()

        duplicate_count = 0
        for candidate in normalized:
            existing = existing_by_event_time.get(candidate.event_time)
            if existing is not None:
                duplicate_count += 1
                self._session.add(
                    ImportRecord(
                        import_run_id=import_run.id,
                        source_row_number=candidate.source_row_number,
                        source_record_id=candidate.source_record_id,
                        normalized_payload_hash=candidate.normalized_payload_hash,
                        event_time=candidate.event_time,
                        disposition="DUPLICATE_IDENTICAL",
                        canonical_bar_id=existing.id,
                        evidence="matches an existing canonical observation",
                    )
                )
                continue
            inserted = inserted_by_event_time[candidate.event_time]
            self._session.add(
                ImportRecord(
                    import_run_id=import_run.id,
                    source_row_number=candidate.source_row_number,
                    source_record_id=candidate.source_record_id,
                    normalized_payload_hash=candidate.normalized_payload_hash,
                    event_time=candidate.event_time,
                    disposition="INSERTED",
                    canonical_bar_id=inserted.id,
                )
            )

        now = _utc_now()
        import_run.status = "SUCCEEDED"
        import_run.effect = "APPLIED" if inserted_by_event_time else "NOOP"
        import_run.rows_read = len(normalized)
        import_run.rows_accepted = len(normalized)
        import_run.rows_rejected = 0
        import_run.rows_inserted = len(inserted_by_event_time)
        import_run.rows_duplicate_identical = duplicate_count
        import_run.rows_conflicted = 0
        import_run.event_time_from = min(bar.event_time for bar in normalized)
        import_run.event_time_to = max(bar.event_time for bar in normalized)
        import_run.trading_day_from = min(bar.trading_day for bar in normalized)
        import_run.trading_day_to = max(bar.trading_day for bar in normalized)
        import_run.available_at_from = min(bar.available_at for bar in normalized)
        import_run.available_at_to = max(bar.available_at for bar in normalized)
        import_run.finished_at = now
        job.status = "SUCCEEDED"
        job.finished_at = now
        self._session.commit()
        return _result_from_import_run(import_run, replayed=False)

    def _assert_mutation_permitted(self) -> None:
        """Run an optional owner guard inside the import write transaction.

        File/profile callers do not provide a guard.  Provider orchestration
        uses it to lock and verify its outer retrieval reservation immediately
        before the durable receipt/import write begins, preventing a manually
        recovered stale parent from being revived by a delayed worker.
        """

        if self._mutation_guard is not None:
            self._mutation_guard()

    def _ensure_receipt(self, command: OhlcvImportCommand, payload: SourcePayload) -> SourceReceipt:
        receipt = self._session.scalar(
            select(SourceReceipt).where(
                SourceReceipt.source_name == command.source_name,
                SourceReceipt.content_hash == payload.content_hash,
                SourceReceipt.source_timezone_name == command.source_timezone_name,
            )
        )
        if receipt is not None:
            if (
                receipt.byte_count != payload.byte_count
                or receipt.media_type != self._adapter.media_type
                or receipt.input_kind != self._adapter.input_kind
                or receipt.source_timezone_name != command.source_timezone_name
                or receipt.retention_policy != self._adapter.retention_policy
                or receipt.acquisition_use != self._adapter.acquisition_use
                or receipt.redistribution_policy != self._adapter.redistribution_policy
            ):
                raise OhlcvImportError(
                    "SOURCE_RECEIPT_METADATA_MISMATCH",
                    "an existing source receipt has conflicting parsing metadata",
                )
            return receipt
        receipt = SourceReceipt(
            source_name=command.source_name,
            content_hash=payload.content_hash,
            media_type=self._adapter.media_type,
            byte_count=payload.byte_count,
            input_kind=self._adapter.input_kind,
            source_timezone_name=command.source_timezone_name,
            retention_policy=self._adapter.retention_policy,
            acquisition_use=self._adapter.acquisition_use,
            redistribution_policy=self._adapter.redistribution_policy,
        )
        self._session.add(receipt)
        self._session.flush()
        return receipt

    def _create_attempt(
        self,
        *,
        command: OhlcvImportCommand,
        receipt: SourceReceipt,
        series: DataSeries | None,
        request_fingerprint: str,
        mapping: dict[str, object],
    ) -> tuple[JobRun, ImportRun]:
        now = _utc_now()
        job = JobRun(
            job_kind=self._adapter.job_kind,
            idempotency_key=command.idempotency_key,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
            status="QUEUED",
        )
        self._session.add(job)
        self._session.flush()
        import_run = ImportRun(
            job_run_id=job.id,
            series_id=series.id if series is not None else None,
            source_receipt_id=receipt.id,
            source_name=command.source_name,
            request_fingerprint=request_fingerprint,
            mapping_version=self._adapter.mapping_version,
            mapping_hash=_stable_hash(mapping),
            mapping=mapping,
            source_timezone_name=command.source_timezone_name,
            status="PENDING",
        )
        self._session.add(import_run)
        self._session.flush()
        job.status = "RUNNING"
        job.started_at = now
        import_run.status = "RUNNING"
        import_run.started_at = now
        return job, import_run

    def _lock_active_series(self, series_id: UUID, *, rows_read: int) -> DataSeries:
        statement = (
            select(DataSeries)
            .where(DataSeries.id == series_id)
            .options(
                joinedload(DataSeries.contract).joinedload(FuturesContract.product),
                joinedload(DataSeries.calendar),
            )
            # PostgreSQL rejects a bare FOR UPDATE when joined eager loading
            # introduces nullable outer-join targets.  The serialization
            # boundary is the series itself; its catalog relations are read
            # only, so lock only the authoritative DataSeries row.
            .with_for_update(of=DataSeries)
        )
        series = self._session.scalar(statement)
        if series is None:
            raise OhlcvImportError(
                "UNKNOWN_SERIES",
                "the requested data series is not cataloged",
                rows_read=rows_read,
                rows_rejected=rows_read,
            )
        if series.status != "ACTIVE":
            raise OhlcvImportError(
                "SERIES_NOT_ACTIVE",
                "the requested data series is not active for ingestion",
                rows_read=rows_read,
                rows_rejected=rows_read,
            )
        if series.volume_unit is None or series.turnover_currency is None:
            raise OhlcvImportError(
                "SERIES_UNIT_CONFIGURATION_MISSING",
                "the requested data series lacks volume/turnover unit metadata",
                rows_read=rows_read,
                rows_rejected=rows_read,
            )
        return series

    def _existing_bars(
        self, series_id: UUID, event_times: Iterable[datetime]
    ) -> dict[datetime, CanonicalBar]:
        existing: dict[datetime, CanonicalBar] = {}
        for chunk in _chunks(tuple(event_times), _BATCH_SIZE):
            rows = self._session.scalars(
                select(CanonicalBar).where(
                    CanonicalBar.series_id == series_id,
                    CanonicalBar.event_time.in_(chunk),
                )
            ).all()
            for bar in rows:
                key = self._canonical_event_key(bar)
                current = existing.get(key)
                if current is None or bar.revision_number > current.revision_number:
                    existing[key] = bar
        return existing

    def _canonical_event_key(self, bar: CanonicalBar) -> datetime:
        """Return the stored canonical instant, rejecting corrupt authority data."""

        event_time = bar.event_time
        if event_time.tzinfo is not None and event_time.utcoffset() is not None:
            return event_time
        raise OhlcvImportError(
            "INVALID_CANONICAL_TIMESTAMP",
            "an existing canonical observation has a timezone-naive event_time",
            quarantined=True,
        )

    def _record_conflicted_batch(
        self,
        import_run: ImportRun,
        normalized: Sequence[NormalizedBar],
        existing_by_event_time: dict[datetime, CanonicalBar],
        conflicts: dict[datetime, CanonicalBar],
    ) -> None:
        for candidate in normalized:
            conflicting = conflicts.get(candidate.event_time)
            existing = existing_by_event_time.get(candidate.event_time)
            conflicting_bar_id: UUID | None
            if conflicting is not None:
                disposition = "CONFLICT"
                error_code = "CANONICAL_BAR_CONFLICT"
                evidence = "canonical key conflicts with an existing observation"
                conflicting_bar_id = conflicting.id
            else:
                disposition = "REJECTED"
                error_code = "BATCH_ABORTED_BY_CONFLICT"
                evidence = "batch was not partially committed after a canonical conflict"
                conflicting_bar_id = existing.id if existing is not None else None
            self._session.add(
                ImportRecord(
                    import_run_id=import_run.id,
                    source_row_number=candidate.source_row_number,
                    source_record_id=candidate.source_record_id,
                    normalized_payload_hash=candidate.normalized_payload_hash,
                    event_time=candidate.event_time,
                    disposition=disposition,
                    conflicting_bar_id=conflicting_bar_id,
                    error_code=error_code,
                    evidence=evidence,
                )
            )


class _SessionWindow:
    """A timezone-safe read model for one explicit materialized session."""

    def __init__(self, *, trading_day: date, opens_at: datetime, closes_at: datetime) -> None:
        self.trading_day = trading_day
        self.opens_at = opens_at
        self.closes_at = closes_at


@dataclass(frozen=True)
class _ValidatedNumericValues:
    """Fixed-decimal values after scale, range, and cross-field validation."""

    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal | None
    open_interest: Decimal | None


def _validate_command(command: OhlcvImportCommand) -> OhlcvImportCommand:
    source_name = command.source_name.strip().upper()
    if not _SOURCE_NAME_PATTERN.fullmatch(source_name) or len(source_name) > 128:
        raise OhlcvImportError(
            "INVALID_SOURCE_NAME",
            "source_name must use uppercase letters, digits, '.', '_' or '-'",
        )
    _load_timezone(command.source_timezone_name)
    idempotency_key = _require_opaque_identifier(command.idempotency_key, "idempotency_key")
    correlation_id = _require_opaque_identifier(command.correlation_id, "correlation_id")
    causation_id = (
        _require_opaque_identifier(command.causation_id, "causation_id")
        if command.causation_id is not None
        else None
    )
    return replace(
        command,
        source_name=source_name,
        source_timezone_name=command.source_timezone_name.strip(),
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def _validate_adapter_source_policy(adapter: CanonicalOhlcvInputAdapter) -> None:
    """Reject an adapter whose fixed policy falls outside the current policy."""

    if adapter.acquisition_use not in SOURCE_RECEIPT_ACQUISITION_USES:
        raise OhlcvImportError(
            "INVALID_SOURCE_RECEIPT_POLICY",
            "the input adapter declares an unsupported acquisition-use policy",
        )
    if adapter.redistribution_policy not in SOURCE_RECEIPT_REDISTRIBUTION_POLICIES:
        raise OhlcvImportError(
            "INVALID_SOURCE_RECEIPT_POLICY",
            "the input adapter declares an unsupported redistribution policy",
        )


def _require_opaque_identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128 or "\x00" in normalized:
        raise OhlcvImportError(
            "INVALID_COMMAND_IDENTIFIER",
            f"{field_name} must contain 1 to 128 non-NUL, non-whitespace characters",
        )
    return normalized


def _request_fingerprint(
    command: OhlcvImportCommand,
    content_hash: str,
    *,
    mapping_version: str,
    request_semantics: dict[str, object] | None,
) -> str:
    fingerprint_input: dict[str, object] = {
        "profile": mapping_version,
        "series_id": str(command.series_id),
        "source_name": command.source_name,
        "source_timezone_name": command.source_timezone_name,
        "source_content_hash": content_hash,
        "available_at_policy": AVAILABLE_AT_POLICY,
        "unit_policy": UNIT_POLICY,
    }
    # Provider adapters bind source-side facts that are not encoded in response
    # bytes, such as publication time. File adapters have no additional facts.
    if request_semantics is not None:
        fingerprint_input["adapter_request_semantics"] = request_semantics
    return _stable_hash(fingerprint_input)


def _enrich_mapping(mapping: dict[str, object], series: DataSeries) -> dict[str, object]:
    assert series.contract is not None
    assert series.contract.product is not None
    assert series.volume_unit is not None
    assert series.turnover_currency is not None
    enriched = dict(mapping)
    enriched["series_id"] = str(series.id)
    enriched["calendar_id"] = str(series.calendar_id)
    enriched["canonical_units"] = {
        "price_currency": series.contract.product.currency,
        "volume_unit": series.volume_unit,
        "open_interest_unit": series.volume_unit,
        "turnover_currency": series.turnover_currency,
        "turnover_multiplier": "1",
    }
    return enriched


def _validate_row_identity(raw: RawOhlcvRow, series: DataSeries, *, rows_read: int) -> None:
    assert series.contract is not None
    assert series.contract.product is not None
    assert series.volume_unit is not None
    assert series.turnover_currency is not None
    if raw.symbol.strip().upper() != series.contract.contract_code:
        raise _row_error(
            "UNKNOWN_OR_MISMATCHED_SYMBOL",
            raw,
            "symbol does not match the cataloged non-continuous contract for this series",
            rows_read=rows_read,
        )
    if raw.interval != series.interval:
        raise _row_error(
            "INTERVAL_MISMATCH",
            raw,
            "input interval does not match the requested cataloged series",
            rows_read=rows_read,
        )
    if raw.price_currency.strip().upper() != series.contract.product.currency:
        raise _row_error(
            "PRICE_CURRENCY_MISMATCH",
            raw,
            "price_currency does not match the cataloged product currency",
            rows_read=rows_read,
        )
    if raw.volume_unit.strip().upper() != series.volume_unit:
        raise _row_error(
            "VOLUME_UNIT_MISMATCH",
            raw,
            "volume_unit does not match the series' declared canonical volume unit",
            rows_read=rows_read,
        )
    if raw.open_interest_unit.strip().upper() != series.volume_unit:
        raise _row_error(
            "OPEN_INTEREST_UNIT_MISMATCH",
            raw,
            "open_interest_unit does not match the series' declared canonical volume unit",
            rows_read=rows_read,
        )
    if raw.turnover_currency.strip().upper() != series.turnover_currency:
        raise _row_error(
            "TURNOVER_CURRENCY_MISMATCH",
            raw,
            "turnover_currency does not match the series' declared canonical turnover currency",
            rows_read=rows_read,
        )
    if raw.turnover_multiplier != Decimal("1"):
        raise _row_error(
            "TURNOVER_MULTIPLIER_UNSUPPORTED",
            raw,
            "this profile only accepts an explicit turnover_multiplier of 1",
            rows_read=rows_read,
        )
    if len(raw.source_record_id) > 256:
        raise _row_error(
            "SOURCE_RECORD_ID_TOO_LONG",
            raw,
            "source_record_id exceeds the supported length",
            rows_read=rows_read,
        )


def _validate_numeric_values(raw: RawOhlcvRow, series: DataSeries) -> _ValidatedNumericValues:
    open_price = _validate_decimal_scale(
        raw.open_price, series.price_scale, _PRICE_PRECISION, raw, "open_price"
    )
    high_price = _validate_decimal_scale(
        raw.high_price, series.price_scale, _PRICE_PRECISION, raw, "high_price"
    )
    low_price = _validate_decimal_scale(
        raw.low_price, series.price_scale, _PRICE_PRECISION, raw, "low_price"
    )
    close_price = _validate_decimal_scale(
        raw.close_price, series.price_scale, _PRICE_PRECISION, raw, "close_price"
    )
    assert series.contract is not None
    assert series.contract.product is not None
    _validate_price_grid(
        raw,
        series.contract.product.price_tick,
        (
            ("open_price", open_price),
            ("high_price", high_price),
            ("low_price", low_price),
            ("close_price", close_price),
        ),
    )
    volume = _validate_decimal_scale(
        raw.volume, series.quantity_scale, _QUANTITY_PRECISION, raw, "volume"
    )
    turnover = (
        _validate_decimal_scale(raw.turnover, _DATABASE_SCALE, _TURNOVER_PRECISION, raw, "turnover")
        if raw.turnover is not None
        else None
    )
    open_interest = (
        _validate_decimal_scale(
            raw.open_interest, series.quantity_scale, _QUANTITY_PRECISION, raw, "open_interest"
        )
        if raw.open_interest is not None
        else None
    )
    if (
        high_price < low_price
        or not (low_price <= open_price <= high_price)
        or not (low_price <= close_price <= high_price)
    ):
        raise _row_error(
            "INVALID_OHLC", raw, "OHLC prices violate the canonical range relationship"
        )
    if volume < 0:
        raise _row_error("NEGATIVE_VOLUME", raw, "volume must not be negative")
    if turnover is not None and turnover < 0:
        raise _row_error("NEGATIVE_TURNOVER", raw, "turnover must not be negative")
    if open_interest is not None and open_interest < 0:
        raise _row_error("NEGATIVE_OPEN_INTEREST", raw, "open_interest must not be negative")
    return _ValidatedNumericValues(
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        turnover=turnover,
        open_interest=open_interest,
    )


def _validate_price_grid(
    raw: RawOhlcvRow,
    price_tick: Decimal,
    prices: Sequence[tuple[str, Decimal]],
) -> None:
    if not price_tick.is_finite() or price_tick <= 0:
        raise _row_error(
            "INVALID_SERIES_PRICE_TICK",
            raw,
            "the cataloged product price_tick must be positive and finite",
        )
    with localcontext() as context:
        context.prec = _PRICE_PRECISION + len(price_tick.as_tuple().digits) + 1
        for field_name, price in prices:
            if price <= 0:
                raise _row_error(
                    "NONPOSITIVE_OHLC_PRICE",
                    raw,
                    f"{field_name} must be greater than zero",
                )
            if price % price_tick != 0:
                raise _row_error(
                    "OHLC_PRICE_OFF_TICK",
                    raw,
                    f"{field_name} must be an exact multiple of product price_tick",
                )


def _validate_decimal_scale(
    value: Decimal,
    scale: int,
    precision: int,
    raw: RawOhlcvRow,
    field_name: str,
) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    with localcontext() as context:
        # ``quantize`` obeys the active Decimal context.  The default precision
        # (28) is smaller than the declared NUMERIC(32, 12) turnover domain, so
        # set a local precision high enough to validate the actual database
        # boundary without changing the caller's global arithmetic context.
        context.prec = max(precision + 1, len(value.as_tuple().digits) + 1)
        try:
            canonical = value.quantize(quantum)
        except InvalidOperation as error:
            raise _row_error(
                "DECIMAL_OUT_OF_RANGE",
                raw,
                f"{field_name} cannot be represented at the declared scale",
            ) from error
    if canonical != value:
        raise _row_error(
            "PRECISION_EXCEEDS_SERIES_SCALE",
            raw,
            f"{field_name} exceeds the declared fixed decimal scale",
        )
    integer_digits = 1 if canonical == 0 else max(1, canonical.adjusted() + 1)
    if integer_digits > precision - _DATABASE_SCALE:
        raise _row_error(
            "DECIMAL_OUT_OF_RANGE", raw, f"{field_name} exceeds the canonical database precision"
        )
    return canonical


def _validate_source_timezone(
    timestamp: datetime, source_timezone: ZoneInfo, raw: RawOhlcvRow, field_name: str
) -> None:
    if timestamp.astimezone(source_timezone).utcoffset() != timestamp.utcoffset():
        raise _row_error(
            "SOURCE_TIMEZONE_OFFSET_MISMATCH",
            raw,
            f"{field_name} offset does not match the declared source timezone",
        )


def _bar_completion_time(
    interval: str, event_time: datetime, day_sessions: Sequence[_SessionWindow]
) -> datetime:
    if interval == "1m":
        return event_time + timedelta(minutes=1)
    if interval == "1d":
        return max(session.closes_at for session in day_sessions)
    raise ValueError(f"unsupported interval: {interval}")


def _group_sessions_by_day(
    sessions: Sequence[_SessionWindow],
) -> dict[date, tuple[_SessionWindow, ...]]:
    result: dict[date, list[_SessionWindow]] = {}
    for session in sessions:
        result.setdefault(session.trading_day, []).append(session)
    return {trading_day: tuple(day_sessions) for trading_day, day_sessions in result.items()}


def _containing_session(
    event_time: datetime, sessions: Sequence[_SessionWindow]
) -> _SessionWindow | None:
    matching = [
        session for session in sessions if session.opens_at <= event_time < session.closes_at
    ]
    if len(matching) > 1:
        raise OhlcvImportError(
            "AMBIGUOUS_TRADING_SESSION",
            "the pinned calendar contains overlapping sessions for this event",
            quarantined=True,
        )
    return matching[0] if matching else None


def _calendar_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value
    raise OhlcvImportError(
        "INVALID_CALENDAR_TIMESTAMP",
        "the pinned trading calendar contains a timezone-naive session boundary",
    )


def _canonical_payload_matches(existing: CanonicalBar, candidate: NormalizedBar) -> bool:
    return existing.normalized_payload_hash == candidate.normalized_payload_hash


def _normalized_payload_hash(
    *,
    series_id: str,
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
    return canonical_observation_payload_hash(
        series_id=series_id,
        event_time=event_time,
        trading_day=trading_day,
        available_at=available_at,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        turnover=turnover,
        open_interest=open_interest,
    )


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _render_decimal(value: Decimal) -> str:
    return format(value, "f")


def _stable_hash(value: dict[str, object]) -> str:
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _row_error(
    code: str,
    raw: RawOhlcvRow,
    detail: str,
    *,
    rows_read: int | None = None,
) -> OhlcvImportError:
    return OhlcvImportError(
        code,
        f"row {raw.source_row_number}: {detail}",
        rows_read=rows_read if rows_read is not None else raw.source_row_number - 1,
        rows_rejected=1,
    )


def _load_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name.strip())
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise OhlcvImportError(
            "UNKNOWN_SOURCE_TIMEZONE", "source_timezone_name must be a known IANA timezone"
        ) from error


def _chunks(values: tuple[datetime, ...], size: int) -> Iterable[tuple[datetime, ...]]:
    iterator = iter(values)
    while chunk := tuple(islice(iterator, size)):
        yield chunk


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _result_from_import_run(
    import_run: ImportRun, *, replayed: bool, effect: str | None = None
) -> OhlcvImportResult:
    return OhlcvImportResult(
        import_run_id=import_run.id,
        job_run_id=import_run.job_run_id,
        source_receipt_id=import_run.source_receipt_id,
        status=import_run.status,
        effect=effect or import_run.effect or "REJECTED",
        rows_read=import_run.rows_read,
        rows_accepted=import_run.rows_accepted,
        rows_rejected=import_run.rows_rejected,
        rows_inserted=import_run.rows_inserted,
        rows_duplicate_identical=import_run.rows_duplicate_identical,
        rows_conflicted=import_run.rows_conflicted,
        replayed=replayed,
    )
