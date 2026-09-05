"""Audited local orchestration for the first SHFE daily retrieval command."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    DataSeries,
    ImportRun,
    JobRun,
    ProviderRetrieval,
    ProviderRetrievalRecovery,
    ShfeDailyRetrievalSourceAdmissionReview,
    ShfeDailySourceAdmissionReview,
)
from northstar_quant.data.core.config import Settings, get_settings
from northstar_quant.data.ingestion.imports import (
    ImportInProgressError,
    OhlcvImportCommand,
    OhlcvImportError,
    OhlcvImportResult,
)
from northstar_quant.data.ingestion.provider_commands import (
    ProviderFetchError,
    RecoverShfeDailyRetrievalCommand,
    ShfeDailyFetchCommand,
)
from northstar_quant.data.ingestion.provider_http import (
    ProviderHttpResponse,
    ShfeDailyHttpClient,
)
from northstar_quant.data.ingestion.providers.shfe import (
    SHFE_DAILY_MAPPING_VERSION,
    SHFE_DAILY_PROFILE_NAME,
    SHFE_DAILY_PROFILE_VERSION,
    ShfeDailyJsonAdapter,
)
from northstar_quant.data.ingestion.service import OhlcvImportService
from northstar_quant.data.ingestion.source_admission import (
    SHFE_DAILY_ACQUISITION_USE,
    SHFE_DAILY_ADAPTER_NAME,
    SHFE_DAILY_ADAPTER_VERSION,
    SHFE_DAILY_AVAILABLE_AT_BASIS,
    SHFE_DAILY_ENDPOINT_ID,
    SHFE_DAILY_REDISTRIBUTION_POLICY,
    SHFE_DAILY_RETENTION_POLICY,
)
from northstar_quant.data.ingestion.source_admission import (
    SHFE_DAILY_MAPPING_VERSION as SHFE_DAILY_ADMISSION_MAPPING_VERSION,
)
from northstar_quant.data.ingestion.source_admission import (
    SHFE_DAILY_SOURCE_NAME as SHFE_DAILY_ADMISSION_SOURCE_NAME,
)
from northstar_quant.data.ingestion.source_admission_service import (
    acquire_shfe_daily_source_admission_review_lock,
)

_JOB_KIND = "SHFE_DAILY_RETRIEVAL_V1"
_SOURCE_NAME = "SHFE_OFFICIAL_DAILY"
_SOURCE_TIMEZONE = "Asia/Shanghai"
_SYMBOL_PATTERN = re.compile(r"[A-Z][A-Z0-9]{1,30}")
_RECOVERY_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RECOVERY_INCIDENT_REFERENCE_PATTERN = re.compile(r"[A-Z]{2,12}-[0-9]{1,20}")
_LOGGER = logging.getLogger(__name__)
_ACTIVE_RETRIEVAL_STATUSES = frozenset({"PENDING", "RUNNING"})
_TERMINAL_IMPORT_STATUSES = frozenset({"SUCCEEDED", "FAILED", "QUARANTINED"})
_STALE_RECOVERY_ERROR_CODE = "RETRIEVAL_STALE_RECOVERED"
_STALE_RECOVERY_DETAIL = (
    "an operator terminalized a stale provider retrieval; see provider retrieval recovery evidence"
)


class _ProviderRetrievalAttemptSuperseded(RuntimeError):
    """Raised when a delayed worker no longer owns its outer reservation."""


@dataclass(frozen=True)
class ProviderRetrievalResult:
    """Safe terminal metadata for one provider retrieval command."""

    retrieval_id: UUID
    job_run_id: UUID
    import_run_id: UUID | None
    source_receipt_id: UUID | None
    status: str
    replayed: bool
    error_code: str | None

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "retrieval_id": str(self.retrieval_id),
            "job_run_id": str(self.job_run_id),
            "import_run_id": str(self.import_run_id) if self.import_run_id else None,
            "source_receipt_id": str(self.source_receipt_id) if self.source_receipt_id else None,
            "status": self.status,
            "replayed": self.replayed,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class ProviderRetrievalRecoveryResult:
    """Safe terminal metadata for a manual stale-retrieval recovery."""

    recovery_id: UUID
    action: str
    retrieval: ProviderRetrievalResult

    def as_dict(self) -> dict[str, str | bool | None]:
        """Return a flat JSON result without source bytes or local paths."""

        result = self.retrieval.as_dict()
        result.update(
            {
                "recovery_id": str(self.recovery_id),
                "recovery_action": self.action,
            }
        )
        return result


class ShfeDailyRetrievalService:
    """Fetch one official SHFE daily response through the canonical write path.

    This is a hand-triggered, one-series, one-trading-day command. It never
    schedules work, accepts arbitrary URLs, guesses source availability, or
    overwrites an existing canonical bar.
    """

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        client: ShfeDailyHttpClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._client = client or ShfeDailyHttpClient(
            timeout_seconds=self._settings.provider_timeout_seconds,
            max_bytes=self._settings.max_provider_response_bytes,
        )

    def retrieve(self, command: ShfeDailyFetchCommand) -> ProviderRetrievalResult:
        """Reserve request evidence, fetch a bounded response, then import it."""

        command = _validate_command(command)
        descriptor = _request_descriptor(command)
        fingerprint = _stable_hash(descriptor)
        existing = self._session.scalar(
            select(ProviderRetrieval).where(ProviderRetrieval.request_fingerprint == fingerprint)
        )
        if existing is not None:
            self._session.rollback()
            return _replay_terminal_or_raise_in_progress(existing)

        # The terminal-replay fast path above is intentionally lock-free. For
        # a genuinely new request, close that read transaction before starting
        # the dedicated read-committed advisory-lock admission transaction.
        self._session.rollback()
        acquire_shfe_daily_source_admission_review_lock(self._session)
        try:
            concurrent = self._session.scalar(
                select(ProviderRetrieval).where(
                    ProviderRetrieval.request_fingerprint == fingerprint
                )
            )
            if concurrent is not None:
                self._session.rollback()
                return _replay_terminal_or_raise_in_progress(concurrent)
            self._assert_target_daily_series(command)
            source_admission_review = self._load_authorizing_source_admission_review(command)
            recovery_parent = self._load_recovery_parent(command, descriptor)
            self._assert_recovery_parent_source_admission_review(
                recovery_parent,
                source_admission_review,
            )
        except BaseException:
            # PostgreSQL's source-admission advisory lock is transaction-scoped.
            # A pre-reservation rejection must release it immediately so a
            # long-lived direct caller cannot block a later review or request.
            self._session.rollback()
            raise
        try:
            retrieval = self._reserve(
                command,
                descriptor,
                fingerprint,
                recovery_parent,
                source_admission_review,
            )
        except IntegrityError as error:
            # The permanent fingerprint reservation is the concurrency fence.
            # Resolve a winner rather than ever issuing the same provider request
            # twice after a simultaneous hand-trigger.
            self._session.rollback()
            concurrent = self._session.scalar(
                select(ProviderRetrieval).where(
                    ProviderRetrieval.request_fingerprint == fingerprint
                )
            )
            if concurrent is not None:
                self._session.rollback()
                return _replay_terminal_or_raise_in_progress(concurrent)
            raise OhlcvImportError(
                "RETRIEVAL_RESERVATION_CONFLICT",
                "the retrieval reservation conflicted; retry the same command safely",
            ) from error
        except BaseException:
            # A source-admission review can expire during the short in-process interval
            # between preflight and reservation. Do not leave a partially
            # flushed JobRun/retrieval or its transaction-scoped advisory lock
            # behind when the final pre-commit admission check rejects it.
            self._session.rollback()
            raise
        return self._fetch_and_import(retrieval, command)

    def recover_stale(
        self, command: RecoverShfeDailyRetrievalCommand
    ) -> ProviderRetrievalRecoveryResult:
        """Terminalize one verified-stale reservation without fetching source data.

        A recovery is deliberately not an in-place retry.  It leaves the parent
        request evidence intact, appends an accountable decision, and allows a
        later explicit child request to be reserved only when the input intent
        remains byte-for-byte equivalent at the descriptor level.
        """

        command = _validate_recovery_command(command)
        replay = self._replay_recovery_if_available(command)
        if replay is not None:
            return replay
        try:
            retrieval = self._session.scalar(
                select(ProviderRetrieval)
                .where(ProviderRetrieval.id == command.retrieval_id)
                .with_for_update()
            )
            if retrieval is None:
                raise OhlcvImportError(
                    "UNKNOWN_PROVIDER_RETRIEVAL",
                    "the requested provider retrieval does not exist",
                )

            # A concurrent call with the same recovery key might have committed
            # while this call waited for the parent row lock.  Re-read only
            # after owning that lock so the command can return its durable
            # decision rather than spuriously reporting a non-active parent.
            replay = self._replay_recovery_if_available(command)
            if replay is not None:
                return replay

            if (
                retrieval.source_name != _SOURCE_NAME
                or retrieval.adapter_name != SHFE_DAILY_PROFILE_NAME
            ):
                raise OhlcvImportError(
                    "UNSUPPORTED_PROVIDER_RECOVERY",
                    "only the implemented SHFE daily retrieval can be recovered",
                )
            if retrieval.status not in _ACTIVE_RETRIEVAL_STATUSES:
                raise OhlcvImportError(
                    "RETRIEVAL_NOT_ACTIVE",
                    "only a PENDING or RUNNING provider retrieval can be recovered",
                )
            self._assert_retrieval_is_stale(retrieval)

            inner_import = self._find_inner_provider_import(retrieval)
            if inner_import is not None:
                return self._reconcile_terminal_inner_import(retrieval, inner_import, command)
            if retrieval.import_run_id is not None or retrieval.source_receipt_id is not None:
                raise OhlcvImportError(
                    "RECOVERY_RECONCILIATION_REQUIRED",
                    "active retrieval lineage is incomplete and requires reconciliation "
                    "before recovery",
                )

            recovery = self._record_recovery_event(retrieval, command, action="TERMINALIZED")
            now = _utc_now()
            retrieval.status = "STALE"
            retrieval.finished_at = now
            retrieval.error_code = _STALE_RECOVERY_ERROR_CODE
            retrieval.error_detail = _STALE_RECOVERY_DETAIL
            retrieval.error_retryable = False
            retrieval.job_run.status = "FAILED"
            retrieval.job_run.finished_at = now
            retrieval.job_run.error_code = _STALE_RECOVERY_ERROR_CODE
            self._session.commit()
            return ProviderRetrievalRecoveryResult(
                recovery_id=recovery.id,
                action=recovery.action,
                retrieval=_result_from_retrieval(retrieval, replayed=False),
            )
        except IntegrityError as error:
            self._session.rollback()
            replay = self._replay_recovery_if_available(command)
            if replay is not None:
                return replay
            raise OhlcvImportError(
                "RECOVERY_RESERVATION_CONFLICT",
                "the recovery reservation conflicted; retry the same command safely",
            ) from error

    def _assert_target_daily_series(self, command: ShfeDailyFetchCommand) -> None:
        """Fail before network I/O unless the request targets its one allowed series."""

        series = self._session.get(DataSeries, command.series_id)
        if series is None:
            raise OhlcvImportError("UNKNOWN_SERIES", "the requested data series is not cataloged")
        if series.status != "ACTIVE":
            raise OhlcvImportError(
                "SERIES_NOT_ACTIVE", "the requested data series is not active for ingestion"
            )
        if series.interval != "1d":
            raise OhlcvImportError(
                "UNSUPPORTED_PROVIDER_SERIES_INTERVAL",
                "the SHFE daily adapter accepts only a one-day data series",
            )
        contract_exchange = series.contract.product.exchange
        calendar_exchange = series.calendar.exchange
        if (
            contract_exchange.code != "SHFE"
            or calendar_exchange.code != "SHFE"
            or contract_exchange.id != calendar_exchange.id
        ):
            raise OhlcvImportError(
                "UNSUPPORTED_PROVIDER_EXCHANGE",
                "the SHFE daily adapter requires a data series from the SHFE exchange",
            )
        if series.volume_unit is None or series.turnover_currency is None:
            raise OhlcvImportError(
                "SERIES_UNIT_CONFIGURATION_MISSING",
                "the requested data series lacks canonical volume/turnover unit metadata",
            )
        if series.contract.contract_code != command.source_symbol:
            raise OhlcvImportError(
                "UNKNOWN_OR_MISMATCHED_SYMBOL",
                "source_symbol does not match the cataloged non-continuous contract",
            )

    def _load_recovery_parent(
        self,
        command: ShfeDailyFetchCommand,
        descriptor: dict[str, object],
    ) -> ProviderRetrieval | None:
        """Return a terminalized parent only for an exact one-time child attempt."""

        if command.recovery_of_retrieval_id is None:
            return None
        parent = self._session.get(ProviderRetrieval, command.recovery_of_retrieval_id)
        if parent is None:
            raise OhlcvImportError(
                "UNKNOWN_RECOVERY_PARENT",
                "the requested stale provider retrieval does not exist",
            )
        if parent.status != "STALE":
            raise OhlcvImportError(
                "RECOVERY_PARENT_NOT_STALE",
                "a child retrieval requires an explicitly stale recovery parent",
            )
        if parent.recovery_of_provider_retrieval_id is not None:
            raise OhlcvImportError(
                "RECOVERY_CHAIN_UNSUPPORTED",
                "only one explicit child attempt is allowed from an original stale retrieval",
            )
        if parent.source_name != _SOURCE_NAME or parent.adapter_name != SHFE_DAILY_PROFILE_NAME:
            raise OhlcvImportError(
                "UNSUPPORTED_PROVIDER_RECOVERY",
                "only an SHFE daily retrieval can be used as a recovery parent",
            )
        expected = _descriptor_without_recovery_parent(parent.request_descriptor)
        actual = _descriptor_without_recovery_parent(descriptor)
        if expected != actual:
            raise OhlcvImportError(
                "RECOVERY_REQUEST_MISMATCH",
                "a recovery child must preserve the parent request semantics exactly",
            )
        successor = self._session.scalar(
            select(ProviderRetrieval).where(
                ProviderRetrieval.recovery_of_provider_retrieval_id == parent.id
            )
        )
        if successor is not None:
            raise OhlcvImportError(
                "RECOVERY_CHILD_ALREADY_EXISTS",
                "the stale retrieval already has one controlled child attempt",
            )
        return parent

    def _load_authorizing_source_admission_review(
        self,
        command: ShfeDailyFetchCommand,
    ) -> ShfeDailySourceAdmissionReview:
        """Require a current human review before a *new* source request.

        This is an admission control, not an attempt to parse exchange terms or
        make a legal conclusion in code.  A completed retrieval is intentionally
        replayed before this method runs: renewal/expiry does not rewrite its
        historical acquisition semantics or create a second provider GET.
        """

        if command.source_admission_review_id is None:
            raise OhlcvImportError(
                "SOURCE_ADMISSION_REVIEW_REQUIRED",
                "a source-admission review UUID is required before a new SHFE source request",
            )
        review = self._session.get(
            ShfeDailySourceAdmissionReview,
            command.source_admission_review_id,
        )
        if review is None:
            raise OhlcvImportError(
                "SOURCE_ADMISSION_REVIEW_REQUIRED",
                "the requested source-admission review does not exist",
            )
        if not _admission_review_matches_current_shfe_daily_source(review):
            raise OhlcvImportError(
                "SOURCE_ADMISSION_REVIEW_SCOPE_MISMATCH",
                "the source-admission review does not match the current SHFE daily source",
            )
        newest = self._session.scalar(
            select(ShfeDailySourceAdmissionReview)
            .order_by(ShfeDailySourceAdmissionReview.review_sequence.desc())
            .limit(1)
        )
        if newest is None or newest.id != review.id:
            raise OhlcvImportError(
                "SOURCE_ADMISSION_REVIEW_SUPERSEDED",
                "the requested source-admission review is not the current review record",
            )
        if review.status != "APPROVED":
            raise OhlcvImportError(
                "SOURCE_ADMISSION_REVIEW_NOT_APPROVED",
                "the current source-admission review does not approve a new SHFE request",
            )
        self._assert_source_admission_review_is_currently_valid(review)
        return review

    @staticmethod
    def _assert_source_admission_review_is_currently_valid(
        review: ShfeDailySourceAdmissionReview,
    ) -> None:
        """Reject an expiry at either locked preflight or pre-commit admission."""

        if _as_utc(review.valid_until) <= _utc_now():
            raise OhlcvImportError(
                "SOURCE_ADMISSION_REVIEW_EXPIRED",
                "the current source-admission review has expired",
            )

    def _assert_recovery_parent_source_admission_review(
        self,
        recovery_parent: ProviderRetrieval | None,
        review: ShfeDailySourceAdmissionReview,
    ) -> None:
        """Preserve one stale child's exact source-admission decision."""

        if recovery_parent is None:
            return
        parent_link = self._session.scalar(
            select(ShfeDailyRetrievalSourceAdmissionReview).where(
                ShfeDailyRetrievalSourceAdmissionReview.provider_retrieval_id == recovery_parent.id
            )
        )
        if parent_link is None or parent_link.source_admission_review_id != review.id:
            raise OhlcvImportError(
                "RECOVERY_SOURCE_ADMISSION_REVIEW_MISMATCH",
                "a recovery child must use the same current source-admission review as its parent",
            )

    def _reserve(
        self,
        command: ShfeDailyFetchCommand,
        descriptor: dict[str, object],
        fingerprint: str,
        recovery_parent: ProviderRetrieval | None,
        source_admission_review: ShfeDailySourceAdmissionReview,
    ) -> ProviderRetrieval:
        existing_job = self._session.scalar(
            select(JobRun).where(
                JobRun.job_kind == _JOB_KIND, JobRun.idempotency_key == command.idempotency_key
            )
        )
        if existing_job is not None:
            raise OhlcvImportError(
                "IDEMPOTENCY_KEY_UNAVAILABLE",
                "the idempotency key is reserved by another provider retrieval",
            )
        now = _utc_now()
        job = JobRun(
            job_kind=_JOB_KIND,
            idempotency_key=command.idempotency_key,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
            status="RUNNING",
            started_at=now,
        )
        self._session.add(job)
        self._session.flush()
        retrieval = ProviderRetrieval(
            job_run_id=job.id,
            series_id=command.series_id,
            source_name=_SOURCE_NAME,
            adapter_name=SHFE_DAILY_PROFILE_NAME,
            adapter_version=SHFE_DAILY_PROFILE_VERSION,
            request_fingerprint=fingerprint,
            request_descriptor=descriptor,
            source_timezone_name=_SOURCE_TIMEZONE,
            status="RUNNING",
            attempt_count=(recovery_parent.attempt_count + 1 if recovery_parent is not None else 1),
            started_at=now,
            recovery_of_provider_retrieval_id=(
                recovery_parent.id if recovery_parent is not None else None
            ),
        )
        self._session.add(retrieval)
        self._session.flush()
        self._session.add(
            ShfeDailyRetrievalSourceAdmissionReview(
                provider_retrieval_id=retrieval.id,
                source_admission_review_id=source_admission_review.id,
            )
        )
        # The lock prevents another review conclusion from interleaving. Time
        # still advances, so re-check validity immediately before turning the
        # pending objects into a durable reservation that may issue its one GET.
        self._assert_source_admission_review_is_currently_valid(source_admission_review)
        self._session.commit()
        return retrieval

    def _fetch_and_import(
        self,
        retrieval: ProviderRetrieval,
        command: ShfeDailyFetchCommand,
    ) -> ProviderRetrievalResult:
        """Run one already-committed retrieval reservation through the write path."""

        try:
            try:
                response = self._client.fetch(command.trading_day)
            except ProviderFetchError as error:
                self._finish_fetch_error(retrieval, error)
                return _result_from_retrieval(retrieval, replayed=False)

            try:
                result = self._import_response(retrieval, command, response)
            except OhlcvImportError as error:
                self._finish_import_exception(retrieval, error)
                return _result_from_retrieval(retrieval, replayed=False)

            self._finish_import_result(retrieval, result)
            return _result_from_retrieval(retrieval, replayed=False)
        except _ProviderRetrievalAttemptSuperseded as error:
            self._session.rollback()
            raise OhlcvImportError(
                "RETRIEVAL_ATTEMPT_SUPERSEDED",
                "the provider retrieval was terminalized before this worker could persist "
                "its result",
            ) from error

    def _import_response(
        self,
        retrieval: ProviderRetrieval,
        command: ShfeDailyFetchCommand,
        response: ProviderHttpResponse,
    ) -> OhlcvImportResult:
        self._lock_active_provider_retrieval(retrieval)
        retrieval.response_http_status = 200
        retrieval.response_content_type = _bounded_header(response.content_type, 128)
        retrieval.response_etag = _bounded_header(response.etag, 512)
        retrieval.response_last_modified = _bounded_header(response.last_modified, 128)
        retrieval.provider_request_id = _bounded_header(response.provider_request_id, 256)
        self._session.commit()

        adapter = ShfeDailyJsonAdapter(
            trading_day=command.trading_day,
            source_symbol=command.source_symbol,
            available_at=command.available_at,
            max_bytes=self._settings.max_provider_response_bytes,
            max_rows=self._settings.max_provider_response_rows,
        )
        staged_path = _stage_private_bytes(response.content)
        try:
            import_command = OhlcvImportCommand(
                file_path=staged_path,
                series_id=command.series_id,
                source_name=_SOURCE_NAME,
                source_timezone_name=_SOURCE_TIMEZONE,
                idempotency_key=f"provider-import-{retrieval.id}",
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            return OhlcvImportService(
                self._session,
                adapter=adapter,
                mutation_guard=lambda: self._lock_active_provider_retrieval(retrieval),
            ).import_file(import_command)
        finally:
            _remove_staged_file(staged_path)

    def _finish_fetch_error(self, retrieval: ProviderRetrieval, error: ProviderFetchError) -> None:
        self._lock_active_provider_retrieval(retrieval)
        now = _utc_now()
        retrieval.status = "FAILED"
        retrieval.response_http_status = error.http_status
        retrieval.error_code = error.code
        retrieval.error_detail = error.detail
        retrieval.error_retryable = error.retryable
        retrieval.finished_at = now
        retrieval.job_run.status = "FAILED"
        retrieval.job_run.error_code = error.code
        retrieval.job_run.finished_at = now
        self._session.commit()

    def _finish_import_exception(
        self, retrieval: ProviderRetrieval, error: OhlcvImportError
    ) -> None:
        self._lock_active_provider_retrieval(retrieval)
        now = _utc_now()
        retrieval.status = "QUARANTINED" if error.quarantined else "FAILED"
        retrieval.error_code = error.code
        retrieval.error_detail = error.detail
        retrieval.error_retryable = False
        retrieval.finished_at = now
        retrieval.job_run.status = "FAILED"
        retrieval.job_run.error_code = error.code
        retrieval.job_run.finished_at = now
        self._session.commit()

    def _finish_import_result(
        self,
        retrieval: ProviderRetrieval,
        result: OhlcvImportResult,
    ) -> None:
        """Attach the exact terminal import outcome to provider evidence.

        Keep the provider request lineage while surfacing the bounded inner
        profile/error code, which lets operators distinguish source decoding
        drift from a canonical-data conflict.
        """

        self._lock_active_provider_retrieval(retrieval)
        import_run = self._session.get(ImportRun, result.import_run_id)
        now = _utc_now()
        retrieval.source_receipt_id = result.source_receipt_id
        retrieval.import_run_id = result.import_run_id
        retrieval.status = result.status
        retrieval.finished_at = now
        if result.status == "SUCCEEDED":
            retrieval.error_code = None
            retrieval.error_detail = None
            retrieval.error_retryable = None
            retrieval.job_run.status = "SUCCEEDED"
            retrieval.job_run.error_code = None
        else:
            retrieval.error_code = (
                import_run.error_code
                if import_run is not None and import_run.error_code
                else "IMPORT_REJECTED"
            )
            retrieval.error_detail = (
                import_run.error_detail
                if import_run is not None and import_run.error_detail
                else "the provider response was rejected by canonical import validation"
            )
            retrieval.error_retryable = False
            retrieval.job_run.status = "FAILED"
            retrieval.job_run.error_code = retrieval.error_code
        retrieval.job_run.finished_at = now
        self._session.commit()

    def _lock_active_provider_retrieval(self, retrieval: ProviderRetrieval) -> None:
        """Lock the running parent before a delayed worker can mutate evidence.

        The lock is held for the entire inner ``OhlcvImportService`` write
        transaction via its mutation guard.  Recovery either waits for that
        transaction and reconciles its terminal import, or wins first and
        causes the delayed worker to fail before it can create new lineage.
        """

        active_id = self._session.scalar(
            select(ProviderRetrieval.id)
            .where(
                ProviderRetrieval.id == retrieval.id,
                ProviderRetrieval.status == "RUNNING",
            )
            .with_for_update()
        )
        if active_id is None:
            raise _ProviderRetrievalAttemptSuperseded()
        self._session.refresh(retrieval)

    def _assert_retrieval_is_stale(self, retrieval: ProviderRetrieval) -> None:
        """Require a configured age threshold before an operator can intervene."""

        observed_at = retrieval.started_at or retrieval.created_at
        if observed_at is None:
            raise OhlcvImportError(
                "RETRIEVAL_STALENESS_UNKNOWN",
                "the active retrieval has no durable start or creation time",
            )
        age = _utc_now() - _as_utc(observed_at)
        threshold = timedelta(seconds=self._settings.provider_retrieval_stale_after_seconds)
        if age < threshold:
            raise OhlcvImportError(
                "RETRIEVAL_NOT_STALE",
                "the active retrieval has not reached the configured stale threshold",
            )

    def _find_inner_provider_import(self, retrieval: ProviderRetrieval) -> ImportRun | None:
        """Find the durable inner import used by a parent retrieval, if any."""

        return self._session.scalar(
            select(ImportRun)
            .join(JobRun, ImportRun.job_run_id == JobRun.id)
            .where(
                JobRun.job_kind == ShfeDailyJsonAdapter.job_kind,
                JobRun.idempotency_key == f"provider-import-{retrieval.id}",
            )
        )

    def _reconcile_terminal_inner_import(
        self,
        retrieval: ProviderRetrieval,
        import_run: ImportRun,
        command: RecoverShfeDailyRetrievalCommand,
    ) -> ProviderRetrievalRecoveryResult:
        """Close a crash window without another source request or new import."""

        if import_run.series_id != retrieval.series_id:
            raise OhlcvImportError(
                "RECOVERY_RECONCILIATION_REQUIRED",
                "the inner provider import belongs to a different data series",
            )
        if (
            import_run.source_name != _SOURCE_NAME
            or import_run.source_timezone_name != _SOURCE_TIMEZONE
            or import_run.mapping_version != SHFE_DAILY_MAPPING_VERSION
        ):
            raise OhlcvImportError(
                "RECOVERY_RECONCILIATION_REQUIRED",
                "the inner import does not match the SHFE retrieval identity",
            )
        if import_run.status not in _TERMINAL_IMPORT_STATUSES:
            raise OhlcvImportError(
                "RECOVERY_RECONCILIATION_REQUIRED",
                "the inner provider import is still active and cannot be recovered safely",
            )
        if import_run.source_receipt_id is None:
            raise OhlcvImportError(
                "RECOVERY_RECONCILIATION_REQUIRED",
                "the terminal inner import lacks source-receipt lineage",
            )
        if retrieval.import_run_id not in {None, import_run.id}:
            raise OhlcvImportError(
                "RECOVERY_RECONCILIATION_REQUIRED",
                "the active retrieval points to a different import run",
            )
        if retrieval.source_receipt_id not in {None, import_run.source_receipt_id}:
            raise OhlcvImportError(
                "RECOVERY_RECONCILIATION_REQUIRED",
                "the active retrieval points to a different source receipt",
            )

        recovery = self._record_recovery_event(retrieval, command, action="RECONCILED_IMPORT")
        retrieval.import_run_id = import_run.id
        retrieval.source_receipt_id = import_run.source_receipt_id
        retrieval.status = import_run.status
        retrieval.finished_at = import_run.finished_at or _utc_now()
        if import_run.status == "SUCCEEDED":
            retrieval.error_code = None
            retrieval.error_detail = None
            retrieval.error_retryable = None
            retrieval.job_run.status = "SUCCEEDED"
            retrieval.job_run.error_code = None
        else:
            retrieval.error_code = import_run.error_code or "IMPORT_REJECTED"
            retrieval.error_detail = (
                import_run.error_detail
                or "the provider response was rejected by canonical import validation"
            )
            retrieval.error_retryable = False
            retrieval.job_run.status = "FAILED"
            retrieval.job_run.error_code = retrieval.error_code
        retrieval.job_run.finished_at = retrieval.finished_at
        self._session.commit()
        return ProviderRetrievalRecoveryResult(
            recovery_id=recovery.id,
            action=recovery.action,
            retrieval=_result_from_retrieval(retrieval, replayed=False),
        )

    def _record_recovery_event(
        self,
        retrieval: ProviderRetrieval,
        command: RecoverShfeDailyRetrievalCommand,
        *,
        action: str,
    ) -> ProviderRetrievalRecovery:
        """Capture pre-recovery state before the parent status is changed."""

        recovery = ProviderRetrievalRecovery(
            provider_retrieval_id=retrieval.id,
            action=action,
            prior_status=retrieval.status,
            prior_attempt_count=retrieval.attempt_count,
            prior_started_at=retrieval.started_at,
            prior_finished_at=retrieval.finished_at,
            prior_response_http_status=retrieval.response_http_status,
            prior_error_code=retrieval.error_code,
            prior_error_detail=retrieval.error_detail,
            operator_id=command.operator_id,
            reason=command.reason,
            idempotency_key=command.idempotency_key,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
        )
        self._session.add(recovery)
        self._session.flush()
        return recovery

    def _replay_recovery_if_available(
        self, command: RecoverShfeDailyRetrievalCommand
    ) -> ProviderRetrievalRecoveryResult | None:
        """Return an identical committed recovery decision for a safe CLI retry."""

        recovery = self._session.scalar(
            select(ProviderRetrievalRecovery).where(
                ProviderRetrievalRecovery.idempotency_key == command.idempotency_key
            )
        )
        if recovery is None:
            return None
        if not _recovery_command_matches(recovery, command):
            raise OhlcvImportError(
                "IDEMPOTENCY_KEY_REUSED",
                "the recovery idempotency key was already used for a different intent",
            )
        retrieval = self._session.get(ProviderRetrieval, recovery.provider_retrieval_id)
        if retrieval is None:  # pragma: no cover - restrictive foreign key invariant
            raise OhlcvImportError(
                "RECOVERY_EVIDENCE_INVALID",
                "the recovery evidence has no provider retrieval parent",
            )
        result = ProviderRetrievalRecoveryResult(
            recovery_id=recovery.id,
            action=recovery.action,
            retrieval=_result_from_retrieval(retrieval, replayed=True),
        )
        # Do not leave an open read transaction behind when the CLI immediately
        # returns a replay.  All result fields were copied into frozen values.
        self._session.rollback()
        return result


def _validate_command(command: ShfeDailyFetchCommand) -> ShfeDailyFetchCommand:
    source_symbol = command.source_symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(source_symbol):
        raise OhlcvImportError(
            "INVALID_SOURCE_SYMBOL", "source_symbol must be an uppercase SHFE contract"
        )
    if command.available_at.tzinfo is None or command.available_at.utcoffset() is None:
        raise OhlcvImportError(
            "MISSING_SOURCE_AVAILABILITY_TIME",
            "available_at must be an explicit RFC 3339 source publication time",
        )
    try:
        source_timezone = ZoneInfo(_SOURCE_TIMEZONE)
    except ZoneInfoNotFoundError as error:  # pragma: no cover - bundled tzdata failure
        raise OhlcvImportError(
            "UNKNOWN_SOURCE_TIMEZONE", "Asia/Shanghai must be available"
        ) from error
    if (
        command.available_at.astimezone(source_timezone).utcoffset()
        != command.available_at.utcoffset()
    ):
        raise OhlcvImportError(
            "SOURCE_TIMEZONE_OFFSET_MISMATCH",
            "available_at must carry the Asia/Shanghai source offset",
        )
    _require_identifier(command.idempotency_key, "idempotency_key")
    _require_identifier(command.correlation_id, "correlation_id")
    if command.causation_id is not None:
        _require_identifier(command.causation_id, "causation_id")
    return ShfeDailyFetchCommand(
        series_id=command.series_id,
        source_symbol=source_symbol,
        trading_day=command.trading_day,
        available_at=command.available_at,
        idempotency_key=command.idempotency_key.strip(),
        correlation_id=command.correlation_id.strip(),
        source_admission_review_id=command.source_admission_review_id,
        causation_id=command.causation_id.strip() if command.causation_id else None,
        recovery_of_retrieval_id=command.recovery_of_retrieval_id,
    )


def _validate_recovery_command(
    command: RecoverShfeDailyRetrievalCommand,
) -> RecoverShfeDailyRetrievalCommand:
    """Accept constrained durable recovery metadata instead of note-like source text."""

    operator_id = _require_recovery_identifier(command.operator_id, "operator_id")
    idempotency_key = _require_recovery_identifier(command.idempotency_key, "idempotency_key")
    correlation_id = _require_recovery_identifier(command.correlation_id, "correlation_id")
    causation_id = (
        _require_recovery_identifier(command.causation_id, "causation_id")
        if command.causation_id is not None
        else None
    )
    reason = command.reason.strip()
    if command.reason != reason or not _RECOVERY_INCIDENT_REFERENCE_PATTERN.fullmatch(reason):
        raise OhlcvImportError(
            "INVALID_RECOVERY_INCIDENT_REFERENCE",
            "reason must be a controlled uppercase incident reference such as QDH-20260902",
        )
    return RecoverShfeDailyRetrievalCommand(
        retrieval_id=command.retrieval_id,
        operator_id=operator_id,
        reason=reason,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def _recovery_command_matches(
    recovery: ProviderRetrievalRecovery,
    command: RecoverShfeDailyRetrievalCommand,
) -> bool:
    """Require a repeated recovery key to describe the same accountable action."""

    return (
        recovery.provider_retrieval_id == command.retrieval_id
        and recovery.operator_id == command.operator_id
        and recovery.reason == command.reason
        and recovery.correlation_id == command.correlation_id
        and recovery.causation_id == command.causation_id
    )


def _require_recovery_identifier(value: str, field_name: str) -> str:
    """Normalize one opaque audit identifier without accepting note-like text.

    Recovery audit fields are persisted indefinitely.  Keep them deliberately
    narrower than ordinary provider command identifiers so an operator cannot
    accidentally place a URL, header, local path, or free-form credential note
    in the recovery evidence plane.
    """

    normalized = value.strip()
    if value != normalized or not _RECOVERY_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OhlcvImportError(
            "INVALID_RECOVERY_IDENTIFIER",
            f"{field_name} must use 1 to 128 letters, digits, '.', '_' or '-'",
        )
    return normalized


def _request_descriptor(command: ShfeDailyFetchCommand) -> dict[str, object]:
    """Return the hashable request intent without a URL, credentials, or headers."""

    descriptor: dict[str, object] = {
        "provider": _SOURCE_NAME,
        "adapter": {"name": SHFE_DAILY_PROFILE_NAME, "version": SHFE_DAILY_PROFILE_VERSION},
        "response_profile": SHFE_DAILY_MAPPING_VERSION,
        "endpoint_id": "shfe_daily_data_v1",
        "series_id": str(command.series_id),
        "source_symbol": command.source_symbol,
        "interval": "1d",
        "trading_day": command.trading_day.isoformat(),
        "available_at": command.available_at.astimezone(UTC).isoformat(),
        "source_timezone_name": _SOURCE_TIMEZONE,
        "unit_policy": "exact-declared-units-no-conversion-v1",
    }
    if command.recovery_of_retrieval_id is not None:
        descriptor["recovery_of_provider_retrieval_id"] = str(command.recovery_of_retrieval_id)
    return descriptor


def _descriptor_without_recovery_parent(descriptor: dict[str, object]) -> dict[str, object]:
    """Compare the immutable provider request without the child-only relation."""

    normalized = dict(descriptor)
    normalized.pop("recovery_of_provider_retrieval_id", None)
    return normalized


def _admission_review_matches_current_shfe_daily_source(
    review: ShfeDailySourceAdmissionReview,
) -> bool:
    """Accept only the exact current SHFE admission scope.

    The source-admission review is deliberately not a generic provider configuration
    record.  Comparing every fixed field keeps a direct-database or future
    database-integrity mistake fail-closed before a provider request can be reserved.
    """

    return (
        review.source_name == _SOURCE_NAME == SHFE_DAILY_ADMISSION_SOURCE_NAME
        and review.adapter_name == SHFE_DAILY_PROFILE_NAME == SHFE_DAILY_ADAPTER_NAME
        and review.adapter_version == SHFE_DAILY_PROFILE_VERSION == SHFE_DAILY_ADAPTER_VERSION
        and review.mapping_version
        == SHFE_DAILY_MAPPING_VERSION
        == SHFE_DAILY_ADMISSION_MAPPING_VERSION
        and review.endpoint_id == "shfe_daily_data_v1" == SHFE_DAILY_ENDPOINT_ID
        and review.acquisition_use == SHFE_DAILY_ACQUISITION_USE
        and review.retention_policy == SHFE_DAILY_RETENTION_POLICY
        and review.redistribution_policy == SHFE_DAILY_REDISTRIBUTION_POLICY
        and review.available_at_basis == SHFE_DAILY_AVAILABLE_AT_BASIS
    )


def _stage_private_bytes(content: bytes) -> Path:
    fd: int | None = None
    path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(prefix="qdh-shfe-", suffix=".json")
        path = Path(raw_path)
        os.fchmod(fd, 0o600)
        staged = os.fdopen(fd, "wb")
        fd = None
        with staged:
            staged.write(content)
        return path
    except OSError as error:
        _close_staged_descriptor(fd)
        if path is not None:
            _best_effort_remove_staged_file(path)
        raise OhlcvImportError(
            "PROVIDER_RESPONSE_STAGING_FAILED",
            "the provider response could not be staged transiently",
        ) from error
    except BaseException:
        _close_staged_descriptor(fd)
        if path is not None:
            _best_effort_remove_staged_file(path)
        raise


def _close_staged_descriptor(fd: int | None) -> None:
    """Close an unowned temporary descriptor without masking its primary failure."""

    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _remove_staged_file(path: Path) -> None:
    """Remove the transient response without changing committed import truth.

    ``OhlcvImportService`` commits the receipt/import/bar lineage before it
    returns.  A subsequent filesystem cleanup failure therefore cannot safely
    recast that durable import as failed or detach its retrieval evidence.  It
    is still an operator-visible retention incident, so emit a bounded warning
    without logging response bytes.
    """

    try:
        path.unlink(missing_ok=True)
    except OSError:
        _LOGGER.warning(
            "PROVIDER_RESPONSE_CLEANUP_FAILED: the transient provider response "
            "could not be removed",
            extra={"event_code": "PROVIDER_RESPONSE_CLEANUP_FAILED"},
        )


def _best_effort_remove_staged_file(path: Path) -> None:
    """Avoid obscuring the primary staging failure with cleanup failure."""

    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _bounded_header(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized[:maximum] if normalized else None


def _stable_hash(value: dict[str, object]) -> str:
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _require_identifier(value: str, field_name: str) -> None:
    normalized = value.strip()
    if not normalized or len(normalized) > 128 or "\x00" in normalized:
        raise OhlcvImportError(
            "INVALID_COMMAND_IDENTIFIER",
            f"{field_name} must contain 1 to 128 non-NUL, non-whitespace characters",
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize one PostgreSQL authority timestamp before comparison."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise OhlcvImportError(
            "INVALID_AUTHORITY_TIMESTAMP",
            "provider authority timestamps must be timezone-aware",
        )
    return value.astimezone(UTC)


def _result_from_retrieval(
    retrieval: ProviderRetrieval, *, replayed: bool
) -> ProviderRetrievalResult:
    return ProviderRetrievalResult(
        retrieval_id=retrieval.id,
        job_run_id=retrieval.job_run_id,
        import_run_id=retrieval.import_run_id,
        source_receipt_id=retrieval.source_receipt_id,
        status=retrieval.status,
        replayed=replayed,
        error_code=retrieval.error_code,
    )


def _replay_terminal_or_raise_in_progress(
    retrieval: ProviderRetrieval,
) -> ProviderRetrievalResult:
    """Avoid reporting a crash-left reservation as a completed replay."""

    if retrieval.status in {"PENDING", "RUNNING"}:
        raise ImportInProgressError(
            "RETRIEVAL_IN_PROGRESS",
            "an equivalent provider retrieval is still active; inspect its durable evidence",
        )
    return _result_from_retrieval(retrieval, replayed=True)
