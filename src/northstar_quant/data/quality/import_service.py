"""Deterministic paged integrity evaluation for persisted imports.

This module deliberately streams persisted import evidence. It does not change
the bounded CSV/Parquet ingestion implementation into a streaming importer, and
it never opens the original source input.  The fixed protocol is independent of
its internal page size: every committed fact is fed into a framed canonical hash
in stable keyset order before one immutable conclusion is appended.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from northstar_quant.data.catalog.models import (
    CanonicalBar,
    ImportQualityEvaluation,
    ImportQualityFinding,
    ImportRecord,
    ImportRun,
    SourceReceipt,
)
from northstar_quant.data.quality.evaluations import (
    IMPORT_QUALITY_PAGE_SIZE,
    IMPORT_QUALITY_RULE_SET_NAME,
    IMPORT_QUALITY_RULE_SET_VERSION,
    MAX_IMPORT_QUALITY_INSERTED_BARS,
    MAX_IMPORT_QUALITY_RECORDS,
    ImportQualityCurrentState,
    ImportQualityEvaluationCommand,
    ImportQualityEvaluationError,
    ImportQualityEvaluationResult,
    validate_import_quality_evaluation_command,
)
from northstar_quant.data.quality.import_evidence import (
    SAFE_ERROR_CODE,
    ProviderRetrievalEvidence,
    load_provider_retrieval_evidence,
    load_terminal_import_run,
    terminal_error_category,
)

_STREAM_PROTOCOL_VERSION = "import_integrity/2.0.0"
_MAX_EVIDENCE_SAMPLE_IDENTIFIERS = 8
_MAX_EVIDENCE_SAMPLE_ROWS = 20
_MAX_EVIDENCE_CATEGORIES = 8
_MAX_EVIDENCE_CATEGORY_CHARACTERS = 96
_MAX_EVIDENCE_BYTES = 2048
_EVIDENCE_TRIM_ORDER = (
    "sample_source_row_numbers",
    "sample_provider_retrieval_ids",
    "sample_canonical_bar_ids",
    "sample_import_record_ids",
    "categories",
)


@dataclass(frozen=True)
class _BarFact:
    id: UUID
    series_id: UUID
    import_run_id: UUID | None
    event_time: datetime
    trading_day: date
    available_at: datetime
    source_name: str
    source_timezone_name: str
    source_record_id: str
    source_content_hash: str
    normalized_payload_hash: str | None


@dataclass(frozen=True)
class _RecordFact:
    id: UUID
    source_row_number: int
    source_record_id: str
    normalized_payload_hash: str
    event_time: datetime
    disposition: str
    canonical_bar_id: UUID | None
    conflicting_bar_id: UUID | None
    error_code: str | None
    canonical_bar: _BarFact | None
    conflicting_bar: _BarFact | None


@dataclass(frozen=True)
class _StreamingFindingSpec:
    rule_code: str
    outcome: str
    severity: str
    occurrence_count: int
    evidence_reason: str
    records: tuple[_RecordFact, ...] = ()
    bars: tuple[_BarFact, ...] = ()
    retrieval_ids: tuple[UUID, ...] = ()
    categories: tuple[str, ...] = ()
    categories_truncated: bool = False


@dataclass(frozen=True)
class _StreamingAnalysis:
    findings: tuple[_StreamingFindingSpec, ...]

    @property
    def outcome(self) -> str:
        outcomes = {finding.outcome for finding in self.findings}
        if "UNKNOWN" in outcomes:
            return "UNKNOWN"
        if "FAIL" in outcomes:
            return "FAIL"
        if "WARN" in outcomes:
            return "WARN"
        return "PASS"

    @property
    def delivery_gate(self) -> str:
        return "ELIGIBLE" if self.outcome in {"PASS", "WARN"} else "BLOCKED"


@dataclass(frozen=True)
class _StreamingScan:
    analysis: _StreamingAnalysis
    input_fingerprint: str
    record_count: int
    inserted_bar_count: int


@dataclass
class _BoundedSamples:
    records: list[_RecordFact] = field(default_factory=list)
    bars: list[_BarFact] = field(default_factory=list)
    retrieval_ids: list[UUID] = field(default_factory=list)

    def add_record(self, record: _RecordFact) -> None:
        if len(self.records) < _MAX_EVIDENCE_SAMPLE_IDENTIFIERS:
            self.records.append(record)

    def add_bar(self, bar: _BarFact) -> None:
        if len(self.bars) < _MAX_EVIDENCE_SAMPLE_IDENTIFIERS:
            self.bars.append(bar)

    def add_retrieval_id(self, retrieval_id: UUID) -> None:
        if len(self.retrieval_ids) < _MAX_EVIDENCE_SAMPLE_IDENTIFIERS:
            self.retrieval_ids.append(retrieval_id)


@dataclass
class _BoundedCategories:
    """Retain a deterministic, fixed-size category summary for evidence."""

    values: set[str] = field(default_factory=set)
    truncated: bool = False

    def add(self, category: str) -> None:
        safe_category = category[:_MAX_EVIDENCE_CATEGORY_CHARACTERS]
        if safe_category != category:
            self.truncated = True
        self.values.add(safe_category)
        if len(self.values) > _MAX_EVIDENCE_CATEGORIES:
            self.values = set(sorted(self.values)[:_MAX_EVIDENCE_CATEGORIES])
            self.truncated = True


@dataclass
class _ConsistencyAccumulator:
    occurrence_count: int = 0
    categories: _BoundedCategories = field(default_factory=_BoundedCategories)
    samples: _BoundedSamples = field(default_factory=_BoundedSamples)

    def add(
        self,
        category: str,
        *,
        record: _RecordFact | None = None,
        bar: _BarFact | None = None,
        retrieval_id: UUID | None = None,
        occurrence_count: int = 1,
    ) -> None:
        self.occurrence_count += occurrence_count
        self.categories.add(category)
        if record is not None:
            self.samples.add_record(record)
        if bar is not None:
            self.samples.add_bar(bar)
        if retrieval_id is not None:
            self.samples.add_retrieval_id(retrieval_id)


class _FramedHasher:
    """Hash canonical JSON frames without an ambiguous concatenation boundary."""

    def __init__(self, domain: str) -> None:
        self._hash = hashlib.sha256()
        self.add({"domain": domain})

    def add(self, value: object) -> None:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._hash.update(len(encoded).to_bytes(8, byteorder="big", signed=False))
        self._hash.update(encoded)

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


class _StreamingAccumulator:
    """Keep only bounded state while reproducing import-integrity semantics."""

    def __init__(self, import_run: ImportRun) -> None:
        self.import_run = import_run
        self.receipt = import_run.source_receipt
        self.record_count = 0
        self.inserted_record_count = 0
        self.duplicate_record_count = 0
        self.rejected_record_count = 0
        self.conflict_record_count = 0
        self.rejected_samples = _BoundedSamples()
        self.rejected_categories = _BoundedCategories()
        self.conflict_samples = _BoundedSamples()
        self.duplicate_samples = _BoundedSamples()
        self.out_of_order_count = 0
        self.out_of_order_samples = _BoundedSamples()
        self.consistency = _ConsistencyAccumulator()
        self.previous_event_time: datetime | None = None
        self.accepted_bar_count = 0
        self.accepted_event_time_from: datetime | None = None
        self.accepted_event_time_to: datetime | None = None
        self.accepted_available_at_from: datetime | None = None
        self.accepted_available_at_to: datetime | None = None
        self.accepted_trading_day_from: date | None = None
        self.accepted_trading_day_to: date | None = None
        self.inserted_bar_count = 0

    def observe_record(self, record: _RecordFact) -> None:
        self.record_count += 1
        self._observe_source_order(record)
        if record.source_row_number - 1 > self.import_run.rows_read:
            self.consistency.add("record_source_row_exceeds_rows_read", record=record)

        if record.disposition == "INSERTED":
            self.inserted_record_count += 1
        elif record.disposition == "DUPLICATE_IDENTICAL":
            self.duplicate_record_count += 1
            self.duplicate_samples.add_record(record)
            if record.canonical_bar is not None:
                self.duplicate_samples.add_bar(record.canonical_bar)
        elif record.disposition == "REJECTED":
            self.rejected_record_count += 1
            self.rejected_samples.add_record(record)
            self.rejected_categories.add(terminal_error_category(record.error_code))
        elif record.disposition == "CONFLICT":
            self.conflict_record_count += 1
            self.conflict_samples.add_record(record)
            if record.conflicting_bar is not None:
                self.conflict_samples.add_bar(record.conflicting_bar)
        else:
            self.consistency.add("record_disposition_invalid", record=record)
            return

        expected_bar_id: UUID | None = None
        expected_bar: _BarFact | None = None
        if record.disposition in {"INSERTED", "DUPLICATE_IDENTICAL"}:
            expected_bar_id = record.canonical_bar_id
            expected_bar = record.canonical_bar
        elif record.disposition == "CONFLICT":
            expected_bar_id = record.conflicting_bar_id
            expected_bar = record.conflicting_bar

        if expected_bar_id is None:
            if record.disposition != "REJECTED":
                self.consistency.add("record_referenced_canonical_bar_missing", record=record)
            return
        if expected_bar is None:
            self.consistency.add("record_referenced_canonical_bar_missing", record=record)
            return
        if expected_bar.id != expected_bar_id:
            self.consistency.add(
                "record_referenced_canonical_bar_identity_mismatch",
                record=record,
                bar=expected_bar,
            )
        self._validate_referenced_bar(record, expected_bar)
        if record.disposition in {"INSERTED", "DUPLICATE_IDENTICAL"}:
            self._observe_accepted_bar(expected_bar, record)
        elif expected_bar.normalized_payload_hash is None:
            self.consistency.add(
                "conflict_record_referenced_canonical_bar_payload_hash_missing",
                record=record,
                bar=expected_bar,
            )
        elif expected_bar.normalized_payload_hash == record.normalized_payload_hash:
            self.consistency.add(
                "conflict_record_payload_matches_conflicting_bar",
                record=record,
                bar=expected_bar,
            )

    def observe_inserted_bar(self, bar: _BarFact) -> None:
        self.inserted_bar_count += 1
        if bar.import_run_id != self.import_run.id:
            self.consistency.add("inserted_canonical_bar_import_run_mismatch", bar=bar)
        if self.import_run.series_id is None or bar.series_id != self.import_run.series_id:
            self.consistency.add("inserted_canonical_bar_series_mismatch", bar=bar)
        if bar.source_name != self.import_run.source_name:
            self.consistency.add("inserted_canonical_bar_source_name_mismatch", bar=bar)
        if (
            self.import_run.source_timezone_name is None
            or bar.source_timezone_name != self.import_run.source_timezone_name
        ):
            self.consistency.add("inserted_canonical_bar_timezone_mismatch", bar=bar)
        if self.receipt is not None and bar.source_content_hash != self.receipt.content_hash:
            self.consistency.add("inserted_canonical_bar_receipt_hash_mismatch", bar=bar)
        if bar.normalized_payload_hash is None:
            self.consistency.add("inserted_canonical_bar_payload_hash_missing", bar=bar)

    def observe_provider_evidence(self, evidence: ProviderRetrievalEvidence) -> None:
        if evidence.reconciliation_reason is not None:
            self.consistency.add(
                evidence.reconciliation_reason,
                retrieval_id=evidence.expected_retrieval_id,
            )
        for retrieval in evidence.retrievals:
            if retrieval.import_run_id != self.import_run.id:
                self.consistency.add(
                    "provider_retrieval_import_run_mismatch", retrieval_id=retrieval.id
                )
            if retrieval.series_id != self.import_run.series_id:
                self.consistency.add(
                    "provider_retrieval_series_mismatch", retrieval_id=retrieval.id
                )
            if retrieval.source_receipt_id != self.import_run.source_receipt_id:
                self.consistency.add(
                    "provider_retrieval_receipt_mismatch", retrieval_id=retrieval.id
                )
            if retrieval.source_name != self.import_run.source_name:
                self.consistency.add(
                    "provider_retrieval_source_name_mismatch", retrieval_id=retrieval.id
                )
            if retrieval.source_timezone_name != self.import_run.source_timezone_name:
                self.consistency.add(
                    "provider_retrieval_timezone_mismatch", retrieval_id=retrieval.id
                )
            if retrieval.status in {"PENDING", "RUNNING"}:
                self.consistency.add("provider_retrieval_not_terminal", retrieval_id=retrieval.id)
            if self.import_run.status == "SUCCEEDED" and retrieval.status != "SUCCEEDED":
                self.consistency.add(
                    "successful_import_provider_retrieval_not_succeeded",
                    retrieval_id=retrieval.id,
                )

    def finalize(
        self,
        *,
        actual_record_count: int,
        actual_inserted_bar_count: int,
        inserted_record_link_violations: int,
        inserted_bar_link_violations: int,
        accepted_bar_reuse: bool,
    ) -> _StreamingAnalysis:
        self._collect_run_count_consistency(actual_record_count)
        self._collect_receipt_consistency()
        if actual_inserted_bar_count != self.inserted_bar_count:
            self.consistency.add("inserted_canonical_bar_scan_count_mismatch")
        if self.import_run.status == "SUCCEEDED":
            if actual_inserted_bar_count != self.import_run.rows_inserted:
                self.consistency.add("inserted_canonical_bar_count_mismatch")
        elif actual_inserted_bar_count:
            self.consistency.add(
                "rejected_import_has_canonical_bar",
                occurrence_count=actual_inserted_bar_count,
            )
        if inserted_record_link_violations:
            self.consistency.add(
                "inserted_record_canonical_bar_bijection_mismatch",
                occurrence_count=inserted_record_link_violations,
            )
        if inserted_bar_link_violations:
            self.consistency.add(
                "inserted_canonical_bar_record_bijection_mismatch",
                occurrence_count=inserted_bar_link_violations,
            )
        if accepted_bar_reuse:
            self.consistency.add("successful_import_reuses_canonical_bar")
        if self.import_run.status == "SUCCEEDED":
            self._collect_success_range_consistency()
        return _streaming_findings(self)

    def _observe_source_order(self, record: _RecordFact) -> None:
        if self.previous_event_time is not None:
            try:
                if record.event_time < self.previous_event_time:
                    self.out_of_order_count += 1
                    self.out_of_order_samples.add_record(record)
            except TypeError:
                self.consistency.add("source_event_time_values_not_comparable", record=record)
        self.previous_event_time = record.event_time

    def _validate_referenced_bar(self, record: _RecordFact, bar: _BarFact) -> None:
        if self.import_run.series_id is None or bar.series_id != self.import_run.series_id:
            self.consistency.add(
                "record_referenced_canonical_bar_series_mismatch", record=record, bar=bar
            )
        if bar.event_time != record.event_time:
            self.consistency.add(
                "record_referenced_canonical_bar_event_time_mismatch", record=record, bar=bar
            )
        if record.disposition == "INSERTED" and bar.import_run_id != self.import_run.id:
            self.consistency.add(
                "inserted_record_canonical_bar_import_run_mismatch", record=record, bar=bar
            )
        if record.disposition == "INSERTED" and bar.source_record_id != record.source_record_id:
            self.consistency.add(
                "inserted_record_canonical_bar_source_record_id_mismatch", record=record, bar=bar
            )
        if record.disposition in {"INSERTED", "DUPLICATE_IDENTICAL"}:
            if bar.normalized_payload_hash != record.normalized_payload_hash:
                self.consistency.add(
                    "record_referenced_canonical_bar_payload_hash_mismatch", record=record, bar=bar
                )

    def _observe_accepted_bar(self, bar: _BarFact, record: _RecordFact) -> None:
        self.accepted_bar_count += 1
        try:
            self.accepted_event_time_from = _minimum_datetime(
                self.accepted_event_time_from, bar.event_time
            )
            self.accepted_event_time_to = _maximum_datetime(
                self.accepted_event_time_to, bar.event_time
            )
            self.accepted_available_at_from = _minimum_datetime(
                self.accepted_available_at_from, bar.available_at
            )
            self.accepted_available_at_to = _maximum_datetime(
                self.accepted_available_at_to, bar.available_at
            )
            self.accepted_trading_day_from = _minimum_date(
                self.accepted_trading_day_from, bar.trading_day
            )
            self.accepted_trading_day_to = _maximum_date(
                self.accepted_trading_day_to, bar.trading_day
            )
        except TypeError:
            self.consistency.add(
                "accepted_bar_timestamp_values_not_comparable", record=record, bar=bar
            )

    def _collect_run_count_consistency(self, actual_record_count: int) -> None:
        if self.import_run.status == "SUCCEEDED":
            if self.import_run.effect not in {"APPLIED", "NOOP"}:
                self.consistency.add("succeeded_import_has_invalid_effect")
            elif (self.import_run.rows_inserted > 0 and self.import_run.effect != "APPLIED") or (
                self.import_run.rows_inserted == 0 and self.import_run.effect != "NOOP"
            ):
                self.consistency.add("succeeded_import_effect_does_not_match_insert_count")
            if self.import_run.error_code is not None:
                self.consistency.add("succeeded_import_has_error_code")
            if self.import_run.source_timezone_name is None:
                self.consistency.add("succeeded_import_missing_source_timezone")
            if self.import_run.rows_rejected != 0 or self.import_run.rows_conflicted != 0:
                self.consistency.add("succeeded_import_has_rejected_or_conflicted_count")
            if self.import_run.rows_read != self.import_run.rows_accepted:
                self.consistency.add("succeeded_import_read_accepted_count_mismatch")
            if self.import_run.rows_accepted != (
                self.import_run.rows_inserted + self.import_run.rows_duplicate_identical
            ):
                self.consistency.add("succeeded_import_acceptance_disposition_count_mismatch")
            if actual_record_count != self.import_run.rows_accepted:
                self.consistency.add("succeeded_import_record_count_mismatch")
            if self.inserted_record_count != self.import_run.rows_inserted:
                self.consistency.add("inserted_record_count_mismatch")
            if self.duplicate_record_count != self.import_run.rows_duplicate_identical:
                self.consistency.add("duplicate_record_count_mismatch")
            if self.rejected_record_count or self.conflict_record_count:
                self.consistency.add("succeeded_import_contains_rejected_or_conflicted_record")
            return

        if self.import_run.effect != "REJECTED":
            self.consistency.add("rejected_import_has_invalid_effect")
        if self.import_run.error_code is None or not SAFE_ERROR_CODE.fullmatch(
            self.import_run.error_code
        ):
            self.consistency.add("rejected_import_error_code_missing_or_invalid")
        if (
            self.import_run.rows_accepted != 0
            or self.import_run.rows_inserted != 0
            or self.import_run.rows_duplicate_identical != 0
        ):
            self.consistency.add("rejected_import_contains_accepted_counts")
        if self.import_run.rows_rejected <= 0:
            self.consistency.add("rejected_import_has_no_rejected_count")
        if self.import_run.rows_conflicted > self.import_run.rows_rejected:
            self.consistency.add("rejected_import_conflict_count_exceeds_rejected_count")
        if self.inserted_record_count or self.duplicate_record_count:
            self.consistency.add("rejected_import_contains_accepted_record")
        if actual_record_count > self.import_run.rows_rejected:
            self.consistency.add("rejected_import_record_count_exceeds_rejected_count")

    def _collect_receipt_consistency(self) -> None:
        if self.receipt is None:
            self.consistency.add("source_receipt_missing")
            return
        if self.receipt.source_name != self.import_run.source_name:
            self.consistency.add("source_receipt_source_name_mismatch")
        if (
            self.import_run.source_timezone_name is not None
            and self.receipt.source_timezone_name != self.import_run.source_timezone_name
        ):
            self.consistency.add("source_receipt_timezone_mismatch")

    def _collect_success_range_consistency(self) -> None:
        if self.accepted_bar_count == 0:
            if any(
                value is not None
                for value in (
                    self.import_run.event_time_from,
                    self.import_run.event_time_to,
                    self.import_run.trading_day_from,
                    self.import_run.trading_day_to,
                    self.import_run.available_at_from,
                    self.import_run.available_at_to,
                )
            ):
                self.consistency.add("empty_successful_import_has_nonempty_range")
            return
        if (
            self.import_run.event_time_from != self.accepted_event_time_from
            or self.import_run.event_time_to != self.accepted_event_time_to
        ):
            self.consistency.add("successful_import_event_time_range_mismatch")
        if (
            self.import_run.trading_day_from != self.accepted_trading_day_from
            or self.import_run.trading_day_to != self.accepted_trading_day_to
        ):
            self.consistency.add("successful_import_trading_day_range_mismatch")
        if (
            self.import_run.available_at_from != self.accepted_available_at_from
            or self.import_run.available_at_to != self.accepted_available_at_to
        ):
            self.consistency.add("successful_import_available_at_range_mismatch")


class ImportQualityEvaluationService:
    """Append one conclusion after a bounded paged scan of durable import evidence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def evaluate(self, command: ImportQualityEvaluationCommand) -> ImportQualityEvaluationResult:
        """Persist or replay exactly one import-quality conclusion."""

        command = validate_import_quality_evaluation_command(command)
        self._require_clean_idle_session()
        existing = self._session.scalar(
            select(ImportQualityEvaluation).where(
                ImportQualityEvaluation.idempotency_key == command.idempotency_key
            )
        )
        if existing is not None:
            return self._replay_or_reject(existing, command)

        self._session.rollback()
        self._begin_consistent_snapshot()
        try:
            existing = self._session.scalar(
                select(ImportQualityEvaluation).where(
                    ImportQualityEvaluation.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                return self._replay_or_reject(existing, command)

            import_run = load_terminal_import_run(self._session, command.import_run_id)
            scan = _scan_streaming_import_evidence(self._session, import_run)
            evaluation = ImportQualityEvaluation(
                import_run_id=import_run.id,
                rule_set_name=IMPORT_QUALITY_RULE_SET_NAME,
                rule_set_version=IMPORT_QUALITY_RULE_SET_VERSION,
                input_fingerprint=scan.input_fingerprint,
                observed_status=import_run.status,
                observed_effect=import_run.effect or "REJECTED",
                outcome=scan.analysis.outcome,
                delivery_gate=scan.analysis.delivery_gate,
                rows_read=import_run.rows_read,
                rows_accepted=import_run.rows_accepted,
                rows_rejected=import_run.rows_rejected,
                rows_inserted=import_run.rows_inserted,
                rows_duplicate_identical=import_run.rows_duplicate_identical,
                rows_conflicted=import_run.rows_conflicted,
                record_count=scan.record_count,
                finding_count=len(scan.analysis.findings),
                idempotency_key=command.idempotency_key,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            self._session.add(evaluation)
            self._session.flush()
            for finding in scan.analysis.findings:
                self._session.add(
                    ImportQualityFinding(
                        import_quality_evaluation_id=evaluation.id,
                        rule_code=finding.rule_code,
                        outcome=finding.outcome,
                        severity=finding.severity,
                        occurrence_count=finding.occurrence_count,
                        evidence=_render_streaming_evidence(finding),
                    )
                )
            result = _streaming_result_from_evaluation(evaluation, replayed=False)
            self._session.commit()
            return result
        except IntegrityError as error:
            self._session.rollback()
            concurrent = self._session.scalar(
                select(ImportQualityEvaluation).where(
                    ImportQualityEvaluation.idempotency_key == command.idempotency_key
                )
            )
            if concurrent is not None:
                return self._replay_or_reject(concurrent, command)
            raise ImportQualityEvaluationError(
                "IMPORT_QUALITY_EVALUATION_RESERVATION_CONFLICT",
                (
                    "the streaming import-quality reservation conflicted; "
                    "retry the same command safely"
                ),
            ) from error
        except BaseException:
            self._session.rollback()
            raise

    def _require_clean_idle_session(self) -> None:
        bind = self._session.get_bind()
        if (
            self._session.in_transaction()
            or self._session.new
            or self._session.dirty
            or self._session.deleted
            or (isinstance(bind, Connection) and bind.in_transaction())
        ):
            raise ImportQualityEvaluationError(
                "IMPORT_QUALITY_SESSION_NOT_CLEAN",
                ("import-quality evaluation requires a clean, idle dedicated database session"),
            )

    def _begin_consistent_snapshot(self) -> None:
        self._session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))

    def _replay_or_reject(
        self,
        evaluation: ImportQualityEvaluation,
        command: ImportQualityEvaluationCommand,
    ) -> ImportQualityEvaluationResult:
        if not _streaming_evaluation_matches_command(evaluation, command):
            self._session.rollback()
            raise ImportQualityEvaluationError(
                "IDEMPOTENCY_KEY_REUSED",
                "the import-quality idempotency key was already used for a different intent",
            )
        result = _streaming_result_from_evaluation(evaluation, replayed=True)
        self._session.rollback()
        return result


def current_import_quality_state(
    session: Session, import_run_id: UUID
) -> ImportQualityCurrentState:
    """Recompute the exact current state without appending evidence.

    The scanner remains the single source of truth for its bounded
    keyset order, complete inserted-record/Bar bijection, and framed input
    fingerprint. Snapshot publication calls this in its existing transaction; this
    helper therefore intentionally owns no transaction, idempotency, or write
    behavior of its own.
    """

    import_run = load_terminal_import_run(session, import_run_id)
    scan = _scan_streaming_import_evidence(session, import_run)
    return ImportQualityCurrentState(
        input_fingerprint=scan.input_fingerprint,
        observed_status=import_run.status,
        observed_effect=import_run.effect or "REJECTED",
        outcome=scan.analysis.outcome,
        delivery_gate=scan.analysis.delivery_gate,
        rows_read=import_run.rows_read,
        rows_accepted=import_run.rows_accepted,
        rows_rejected=import_run.rows_rejected,
        rows_inserted=import_run.rows_inserted,
        rows_duplicate_identical=import_run.rows_duplicate_identical,
        rows_conflicted=import_run.rows_conflicted,
        record_count=scan.record_count,
        finding_count=len(scan.analysis.findings),
    )


def _scan_streaming_import_evidence(session: Session, import_run: ImportRun) -> _StreamingScan:
    """Read every bounded durable fact with fixed-order Core/keyset scans."""

    if IMPORT_QUALITY_PAGE_SIZE < 1:
        raise ImportQualityEvaluationError(
            "IMPORT_QUALITY_PROTOCOL_INVALID",
            "the fixed streaming import-quality page size must be positive",
        )
    record_count = _count_records(session, import_run.id)
    if record_count > MAX_IMPORT_QUALITY_RECORDS:
        _raise_streaming_input_too_large("import records", MAX_IMPORT_QUALITY_RECORDS)
    inserted_bar_count = _count_inserted_bars(session, import_run.id)
    if inserted_bar_count > MAX_IMPORT_QUALITY_INSERTED_BARS:
        _raise_streaming_input_too_large(
            "inserted canonical Bars", MAX_IMPORT_QUALITY_INSERTED_BARS
        )

    provider_evidence = load_provider_retrieval_evidence(session, import_run)
    accumulator = _StreamingAccumulator(import_run)
    accumulator.observe_provider_evidence(provider_evidence)
    record_hasher = _FramedHasher("import_integrity_quality/2.0.0/import-record-stream")
    inserted_bar_hasher = _FramedHasher("import_integrity_quality/2.0.0/inserted-bar-stream")
    _scan_records(session, import_run.id, accumulator, record_hasher)
    _scan_inserted_bars(session, import_run.id, accumulator, inserted_bar_hasher)
    inserted_record_link_violations = _count_inserted_record_link_violations(session, import_run.id)
    inserted_bar_link_violations = _count_inserted_bar_link_violations(session, import_run.id)
    accepted_bar_reuse = _has_accepted_bar_reuse(session, import_run.id)
    analysis = accumulator.finalize(
        actual_record_count=record_count,
        actual_inserted_bar_count=inserted_bar_count,
        inserted_record_link_violations=inserted_record_link_violations,
        inserted_bar_link_violations=inserted_bar_link_violations,
        accepted_bar_reuse=accepted_bar_reuse,
    )
    return _StreamingScan(
        analysis=analysis,
        input_fingerprint=_streaming_input_fingerprint(
            import_run=import_run,
            provider_evidence=provider_evidence,
            record_count=record_count,
            inserted_bar_count=inserted_bar_count,
            record_stream_hash=record_hasher.hexdigest(),
            inserted_bar_stream_hash=inserted_bar_hasher.hexdigest(),
        ),
        record_count=record_count,
        inserted_bar_count=inserted_bar_count,
    )


def _count_records(session: Session, import_run_id: UUID) -> int:
    """Count at most one more than the fixed protocol cap.

    An oversized historical or malformed import must be rejected without first
    forcing the evaluator to aggregate an unbounded number of rows.  For an
    admissible input this remains its exact count; for an oversized input the
    returned sentinel is sufficient to fail closed.
    """

    bounded_records = (
        select(ImportRecord.id)
        .where(ImportRecord.import_run_id == import_run_id)
        .limit(MAX_IMPORT_QUALITY_RECORDS + 1)
        .subquery()
    )
    return int(session.scalar(select(func.count()).select_from(bounded_records)) or 0)


def _count_inserted_bars(session: Session, import_run_id: UUID) -> int:
    """Count at most one more than the fixed imported-Bar evidence cap."""

    bounded_bars = (
        select(CanonicalBar.id)
        .where(CanonicalBar.import_run_id == import_run_id)
        .limit(MAX_IMPORT_QUALITY_INSERTED_BARS + 1)
        .subquery()
    )
    return int(session.scalar(select(func.count()).select_from(bounded_bars)) or 0)


def _scan_records(
    session: Session,
    import_run_id: UUID,
    accumulator: _StreamingAccumulator,
    hasher: _FramedHasher,
) -> None:
    canonical = aliased(CanonicalBar, name="stream_canonical_bar")
    conflicting = aliased(CanonicalBar, name="stream_conflicting_bar")
    last_source_row_number: int | None = None
    while True:
        statement = (
            select(
                ImportRecord.id.label("record_id"),
                ImportRecord.source_row_number.label("record_source_row_number"),
                ImportRecord.source_record_id.label("record_source_record_id"),
                ImportRecord.normalized_payload_hash.label("record_normalized_payload_hash"),
                ImportRecord.event_time.label("record_event_time"),
                ImportRecord.disposition.label("record_disposition"),
                ImportRecord.canonical_bar_id.label("record_canonical_bar_id"),
                ImportRecord.conflicting_bar_id.label("record_conflicting_bar_id"),
                ImportRecord.error_code.label("record_error_code"),
                *_bar_select_columns(canonical, "canonical"),
                *_bar_select_columns(conflicting, "conflicting"),
            )
            .select_from(ImportRecord)
            .outerjoin(canonical, canonical.id == ImportRecord.canonical_bar_id)
            .outerjoin(conflicting, conflicting.id == ImportRecord.conflicting_bar_id)
            .where(ImportRecord.import_run_id == import_run_id)
            .order_by(ImportRecord.source_row_number)
            .limit(IMPORT_QUALITY_PAGE_SIZE)
        )
        if last_source_row_number is not None:
            statement = statement.where(ImportRecord.source_row_number > last_source_row_number)
        rows = session.execute(statement).mappings().all()
        if not rows:
            return
        for row in rows:
            record = _record_fact_from_mapping(row)
            hasher.add(_render_record_frame(record))
            accumulator.observe_record(record)
        last_source_row_number = int(rows[-1]["record_source_row_number"])
        if len(rows) < IMPORT_QUALITY_PAGE_SIZE:
            return


def _scan_inserted_bars(
    session: Session,
    import_run_id: UUID,
    accumulator: _StreamingAccumulator,
    hasher: _FramedHasher,
) -> None:
    last_event_time: datetime | None = None
    last_id: UUID | None = None
    while True:
        statement = (
            select(*_bar_select_columns(CanonicalBar, "inserted"))
            .where(CanonicalBar.import_run_id == import_run_id)
            .order_by(CanonicalBar.event_time, CanonicalBar.id)
            .limit(IMPORT_QUALITY_PAGE_SIZE)
        )
        if last_event_time is not None and last_id is not None:
            statement = statement.where(
                or_(
                    CanonicalBar.event_time > last_event_time,
                    and_(CanonicalBar.event_time == last_event_time, CanonicalBar.id > last_id),
                )
            )
        rows = session.execute(statement).mappings().all()
        if not rows:
            return
        for row in rows:
            bar = _bar_fact_from_mapping(row, "inserted")
            assert bar is not None  # all inserted-bar rows originate from CanonicalBar
            hasher.add(_render_bar(bar))
            accumulator.observe_inserted_bar(bar)
        final = _bar_fact_from_mapping(rows[-1], "inserted")
        assert final is not None
        last_event_time = final.event_time
        last_id = final.id
        if len(rows) < IMPORT_QUALITY_PAGE_SIZE:
            return


def _bar_select_columns(alias: Any, prefix: str) -> tuple[Any, ...]:
    return (
        alias.id.label(f"{prefix}_id"),
        alias.series_id.label(f"{prefix}_series_id"),
        alias.import_run_id.label(f"{prefix}_import_run_id"),
        alias.event_time.label(f"{prefix}_event_time"),
        alias.trading_day.label(f"{prefix}_trading_day"),
        alias.available_at.label(f"{prefix}_available_at"),
        alias.source_name.label(f"{prefix}_source_name"),
        alias.source_timezone_name.label(f"{prefix}_source_timezone_name"),
        alias.source_record_id.label(f"{prefix}_source_record_id"),
        alias.source_content_hash.label(f"{prefix}_source_content_hash"),
        alias.normalized_payload_hash.label(f"{prefix}_normalized_payload_hash"),
    )


def _record_fact_from_mapping(row: Any) -> _RecordFact:
    return _RecordFact(
        id=row["record_id"],
        source_row_number=row["record_source_row_number"],
        source_record_id=row["record_source_record_id"],
        normalized_payload_hash=row["record_normalized_payload_hash"],
        event_time=row["record_event_time"],
        disposition=row["record_disposition"],
        canonical_bar_id=row["record_canonical_bar_id"],
        conflicting_bar_id=row["record_conflicting_bar_id"],
        error_code=row["record_error_code"],
        canonical_bar=_bar_fact_from_mapping(row, "canonical"),
        conflicting_bar=_bar_fact_from_mapping(row, "conflicting"),
    )


def _bar_fact_from_mapping(row: Any, prefix: str) -> _BarFact | None:
    bar_id = row[f"{prefix}_id"]
    if bar_id is None:
        return None
    return _BarFact(
        id=bar_id,
        series_id=row[f"{prefix}_series_id"],
        import_run_id=row[f"{prefix}_import_run_id"],
        event_time=row[f"{prefix}_event_time"],
        trading_day=row[f"{prefix}_trading_day"],
        available_at=row[f"{prefix}_available_at"],
        source_name=row[f"{prefix}_source_name"],
        source_timezone_name=row[f"{prefix}_source_timezone_name"],
        source_record_id=row[f"{prefix}_source_record_id"],
        source_content_hash=row[f"{prefix}_source_content_hash"],
        normalized_payload_hash=row[f"{prefix}_normalized_payload_hash"],
    )


def _count_inserted_record_link_violations(session: Session, import_run_id: UUID) -> int:
    """Count missing/reused ``INSERTED`` record links without retaining a set."""

    invalid_links = (
        select(ImportRecord.canonical_bar_id)
        .where(
            ImportRecord.import_run_id == import_run_id,
            ImportRecord.disposition == "INSERTED",
        )
        .group_by(ImportRecord.canonical_bar_id)
        .having(
            or_(
                ImportRecord.canonical_bar_id.is_(None),
                func.count(ImportRecord.id) != 1,
            )
        )
        .subquery()
    )
    return int(session.scalar(select(func.count()).select_from(invalid_links)) or 0)


def _count_inserted_bar_link_violations(session: Session, import_run_id: UUID) -> int:
    """Detect orphan or multiply-linked inserted Bars with one bounded SQL aggregate."""

    linked_records = aliased(ImportRecord, name="stream_inserted_link")
    invalid_links = (
        select(CanonicalBar.id)
        .outerjoin(
            linked_records,
            and_(
                linked_records.import_run_id == import_run_id,
                linked_records.disposition == "INSERTED",
                linked_records.canonical_bar_id == CanonicalBar.id,
            ),
        )
        .where(CanonicalBar.import_run_id == import_run_id)
        .group_by(CanonicalBar.id)
        .having(func.count(linked_records.id) != 1)
        .subquery()
    )
    return int(session.scalar(select(func.count()).select_from(invalid_links)) or 0)


def _has_accepted_bar_reuse(session: Session, import_run_id: UUID) -> bool:
    """Use a grouped existence query rather than an unbounded accepted-Bar set."""

    duplicate = session.scalar(
        select(ImportRecord.canonical_bar_id)
        .where(
            ImportRecord.import_run_id == import_run_id,
            ImportRecord.disposition.in_(("INSERTED", "DUPLICATE_IDENTICAL")),
            ImportRecord.canonical_bar_id.is_not(None),
        )
        .group_by(ImportRecord.canonical_bar_id)
        .having(func.count(ImportRecord.id) > 1)
        .limit(1)
    )
    return duplicate is not None


def _streaming_findings(accumulator: _StreamingAccumulator) -> _StreamingAnalysis:
    findings: list[_StreamingFindingSpec] = []
    if accumulator.consistency.occurrence_count:
        findings.append(
            _StreamingFindingSpec(
                rule_code="IMPORT_EVIDENCE_INCONSISTENT",
                outcome="UNKNOWN",
                severity="ERROR",
                occurrence_count=accumulator.consistency.occurrence_count,
                evidence_reason="terminal_import_evidence_is_missing_or_self_contradictory",
                records=tuple(accumulator.consistency.samples.records),
                bars=tuple(accumulator.consistency.samples.bars),
                retrieval_ids=tuple(accumulator.consistency.samples.retrieval_ids),
                categories=tuple(sorted(accumulator.consistency.categories.values)),
                categories_truncated=accumulator.consistency.categories.truncated,
            )
        )
    if accumulator.import_run.status in {"FAILED", "QUARANTINED"}:
        category = terminal_error_category(accumulator.import_run.error_code)
        findings.append(
            _StreamingFindingSpec(
                rule_code=f"IMPORT_{category}_REJECTED",
                outcome="FAIL",
                severity="ERROR",
                occurrence_count=max(accumulator.import_run.rows_rejected, 1),
                evidence_reason="terminal_import_rejection",
                records=tuple(accumulator.rejected_samples.records)
                + tuple(accumulator.conflict_samples.records),
                categories=(category,),
            )
        )
    if accumulator.conflict_record_count:
        findings.append(
            _StreamingFindingSpec(
                rule_code="CANONICAL_CONFLICT_REJECTED",
                outcome="FAIL",
                severity="ERROR",
                occurrence_count=accumulator.conflict_record_count,
                evidence_reason="recorded_canonical_key_conflict",
                records=tuple(accumulator.conflict_samples.records),
                bars=tuple(accumulator.conflict_samples.bars),
            )
        )
    if accumulator.rejected_record_count:
        categories = tuple(sorted(accumulator.rejected_categories.values))
        findings.append(
            _StreamingFindingSpec(
                rule_code="IMPORT_RECORD_REJECTED",
                outcome="FAIL",
                severity="ERROR",
                occurrence_count=accumulator.rejected_record_count,
                evidence_reason="recorded_normalized_row_rejection",
                records=tuple(accumulator.rejected_samples.records),
                categories=categories,
                categories_truncated=accumulator.rejected_categories.truncated,
            )
        )
    if accumulator.duplicate_record_count:
        findings.append(
            _StreamingFindingSpec(
                rule_code="DUPLICATE_IDENTICAL_OBSERVATION",
                outcome="PASS",
                severity="INFO",
                occurrence_count=accumulator.duplicate_record_count,
                evidence_reason="reused_identical_existing_canonical_observation",
                records=tuple(accumulator.duplicate_samples.records),
                bars=tuple(accumulator.duplicate_samples.bars),
            )
        )
    if accumulator.out_of_order_count:
        findings.append(
            _StreamingFindingSpec(
                rule_code="SOURCE_EVENT_TIME_NON_MONOTONIC",
                outcome="WARN",
                severity="WARNING",
                occurrence_count=accumulator.out_of_order_count,
                evidence_reason="source_row_presentation_order_regresses_in_event_time",
                records=tuple(accumulator.out_of_order_samples.records),
            )
        )
    if not findings:
        findings.append(
            _StreamingFindingSpec(
                rule_code="IMPORT_INTEGRITY_CONFIRMED",
                outcome="PASS",
                severity="INFO",
                occurrence_count=1,
                evidence_reason="terminal_import_counts_lineage_and_record_dispositions_are_consistent",
            )
        )
    return _StreamingAnalysis(findings=tuple(findings))


def _streaming_input_fingerprint(
    *,
    import_run: ImportRun,
    provider_evidence: ProviderRetrievalEvidence,
    record_count: int,
    inserted_bar_count: int,
    record_stream_hash: str,
    inserted_bar_stream_hash: str,
) -> str:
    receipt = import_run.source_receipt
    hasher = _FramedHasher("import_integrity_quality/2.0.0/root")
    hasher.add(
        {
            "rule_set": (f"{IMPORT_QUALITY_RULE_SET_NAME}/{IMPORT_QUALITY_RULE_SET_VERSION}"),
            "protocol": _STREAM_PROTOCOL_VERSION,
        }
    )
    hasher.add({"import_run": _render_import_run(import_run)})
    hasher.add({"source_receipt": _render_receipt(receipt)})
    hasher.add(
        {
            "record_count": record_count,
            "record_stream_hash": record_stream_hash,
            "inserted_bar_count": inserted_bar_count,
            "inserted_bar_stream_hash": inserted_bar_stream_hash,
        }
    )
    for retrieval in provider_evidence.retrievals:
        hasher.add(
            {
                "provider_retrieval": [
                    str(retrieval.id),
                    _string_or_none(retrieval.series_id),
                    _string_or_none(retrieval.import_run_id),
                    _string_or_none(retrieval.source_receipt_id),
                    retrieval.source_name,
                    retrieval.source_timezone_name,
                    retrieval.status,
                ]
            }
        )
    hasher.add(
        {
            "provider_retrieval_reconciliation": {
                "expected_retrieval_id": _string_or_none(provider_evidence.expected_retrieval_id),
                "reason": provider_evidence.reconciliation_reason,
            }
        }
    )
    return hasher.hexdigest()


def _render_import_run(import_run: ImportRun) -> dict[str, object]:
    return {
        "id": str(import_run.id),
        "series_id": _string_or_none(import_run.series_id),
        "source_receipt_id": _string_or_none(import_run.source_receipt_id),
        "source_name": import_run.source_name,
        "source_timezone_name": import_run.source_timezone_name,
        "mapping_version": import_run.mapping_version,
        "mapping_hash": import_run.mapping_hash,
        "status": import_run.status,
        "effect": import_run.effect,
        "rows": [
            import_run.rows_read,
            import_run.rows_accepted,
            import_run.rows_rejected,
            import_run.rows_inserted,
            import_run.rows_duplicate_identical,
            import_run.rows_conflicted,
        ],
        "error_code": _safe_error_code(import_run.error_code),
        "event_time_range": [
            _render_nullable_timestamp(import_run.event_time_from),
            _render_nullable_timestamp(import_run.event_time_to),
        ],
        "trading_day_range": [
            import_run.trading_day_from.isoformat() if import_run.trading_day_from else None,
            import_run.trading_day_to.isoformat() if import_run.trading_day_to else None,
        ],
        "available_at_range": [
            _render_nullable_timestamp(import_run.available_at_from),
            _render_nullable_timestamp(import_run.available_at_to),
        ],
    }


def _render_receipt(receipt: SourceReceipt | None) -> list[object] | None:
    if receipt is None:
        return None
    return [
        str(receipt.id),
        receipt.source_name,
        receipt.content_hash,
        receipt.media_type,
        receipt.byte_count,
        receipt.input_kind,
        receipt.source_timezone_name,
        receipt.retention_policy,
        receipt.acquisition_use,
        receipt.redistribution_policy,
    ]


def _render_record_frame(record: _RecordFact) -> dict[str, object]:
    return {
        "record": [
            str(record.id),
            record.source_row_number,
            record.source_record_id,
            _render_nullable_timestamp(record.event_time),
            record.normalized_payload_hash,
            record.disposition,
            _string_or_none(record.canonical_bar_id),
            _string_or_none(record.conflicting_bar_id),
            _safe_error_code(record.error_code),
        ],
        "canonical_bar": _render_bar(record.canonical_bar),
        "conflicting_bar": _render_bar(record.conflicting_bar),
    }


def _render_bar(bar: _BarFact | None) -> list[object] | None:
    if bar is None:
        return None
    return [
        str(bar.id),
        _string_or_none(bar.series_id),
        _string_or_none(bar.import_run_id),
        _render_nullable_timestamp(bar.event_time),
        bar.trading_day.isoformat(),
        _render_nullable_timestamp(bar.available_at),
        bar.source_name,
        bar.source_timezone_name,
        bar.source_record_id,
        bar.source_content_hash,
        bar.normalized_payload_hash,
    ]


def _render_streaming_evidence(finding: _StreamingFindingSpec) -> str:
    """Render a deterministic, globally bounded evidence projection.

    Individual samples are bounded while the import is scanned, but a single
    consistency finding can legitimately combine every sample family.  Build
    the complete bounded projection first, then remove the last item from a
    fixed-priority list until its serialized form fits the storage limit.
    This preserves stable core metadata and makes the retained evidence
    independent of Python/set iteration order.
    """

    record_ids = sorted({str(record.id) for record in finding.records})[
        :_MAX_EVIDENCE_SAMPLE_IDENTIFIERS
    ]
    source_rows = sorted({record.source_row_number for record in finding.records})[
        :_MAX_EVIDENCE_SAMPLE_ROWS
    ]
    bar_ids = sorted({str(bar.id) for bar in finding.bars})[:_MAX_EVIDENCE_SAMPLE_IDENTIFIERS]
    retrieval_ids = sorted({str(item) for item in finding.retrieval_ids})[
        :_MAX_EVIDENCE_SAMPLE_IDENTIFIERS
    ]
    payload: dict[str, object] = {
        "evidence_version": (f"{IMPORT_QUALITY_RULE_SET_NAME}/{IMPORT_QUALITY_RULE_SET_VERSION}"),
        "protocol": _STREAM_PROTOCOL_VERSION,
        "reason": finding.evidence_reason,
        "occurrence_count": finding.occurrence_count,
    }
    categories = sorted(
        {category[:_MAX_EVIDENCE_CATEGORY_CHARACTERS] for category in finding.categories}
    )
    categories_truncated = (
        finding.categories_truncated
        or len(categories) > _MAX_EVIDENCE_CATEGORIES
        or any(len(category) > _MAX_EVIDENCE_CATEGORY_CHARACTERS for category in finding.categories)
    )
    optional_values: dict[str, list[object]] = {
        "categories": list(categories[:_MAX_EVIDENCE_CATEGORIES]),
        "sample_import_record_ids": list(record_ids),
        "sample_source_row_numbers": list(source_rows),
        "sample_canonical_bar_ids": list(bar_ids),
        "sample_provider_retrieval_ids": list(retrieval_ids),
    }
    return _render_bounded_streaming_evidence(
        payload=payload,
        optional_values=optional_values,
        categories_truncated=categories_truncated,
    )


def _render_bounded_streaming_evidence(
    *,
    payload: dict[str, object],
    optional_values: dict[str, list[object]],
    categories_truncated: bool,
) -> str:
    """Fit optional evidence into the physical 2 KiB finding limit.

    The fixed trim order retains the lowest sorted representatives of each
    family.  Once any representative is removed, ``evidence_truncated`` makes
    that loss explicit.  Categories retain their existing, more specific flag
    because their list can be clipped before this global budget is applied.
    """

    evidence_truncated = False
    while True:
        candidate = dict(payload)
        for evidence_field, values in optional_values.items():
            if values:
                candidate[evidence_field] = values
        if categories_truncated:
            candidate["categories_truncated"] = True
        if evidence_truncated:
            candidate["evidence_truncated"] = True
        rendered = json.dumps(candidate, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if len(rendered.encode("utf-8")) <= _MAX_EVIDENCE_BYTES:
            return rendered

        for evidence_field in _EVIDENCE_TRIM_ORDER:
            values = optional_values[evidence_field]
            if values:
                values.pop()
                evidence_truncated = True
                if evidence_field == "categories":
                    categories_truncated = True
                break
        else:  # pragma: no cover - core protocol values are fixed and bounded
            raise ImportQualityEvaluationError(
                "IMPORT_QUALITY_EVIDENCE_TOO_LARGE",
                (
                    "the streaming import-quality evidence metadata exceeded "
                    "its bounded storage limit"
                ),
            )


def _streaming_evaluation_matches_command(
    evaluation: ImportQualityEvaluation,
    command: ImportQualityEvaluationCommand,
) -> bool:
    return (
        evaluation.import_run_id == command.import_run_id
        and evaluation.rule_set_name == IMPORT_QUALITY_RULE_SET_NAME
        and evaluation.rule_set_version == IMPORT_QUALITY_RULE_SET_VERSION
        and evaluation.correlation_id == command.correlation_id
        and evaluation.causation_id == command.causation_id
    )


def _streaming_result_from_evaluation(
    evaluation: ImportQualityEvaluation, *, replayed: bool
) -> ImportQualityEvaluationResult:
    return ImportQualityEvaluationResult(
        import_quality_evaluation_id=evaluation.id,
        import_run_id=evaluation.import_run_id,
        outcome=evaluation.outcome,
        delivery_gate=evaluation.delivery_gate,
        rows_read=evaluation.rows_read,
        rows_accepted=evaluation.rows_accepted,
        rows_rejected=evaluation.rows_rejected,
        rows_inserted=evaluation.rows_inserted,
        rows_duplicate_identical=evaluation.rows_duplicate_identical,
        rows_conflicted=evaluation.rows_conflicted,
        record_count=evaluation.record_count,
        finding_count=evaluation.finding_count,
        replayed=replayed,
    )


def _raise_streaming_input_too_large(evidence_kind: str, maximum: int) -> None:
    raise ImportQualityEvaluationError(
        "IMPORT_QUALITY_EVALUATION_INPUT_TOO_LARGE",
        (
            "import-quality evaluation accepts at most "
            f"{maximum} {evidence_kind}; a larger import remains blocked for delivery"
        ),
    )


def _safe_error_code(value: str | None) -> str | None:
    return value if value is not None and SAFE_ERROR_CODE.fullmatch(value) else None


def _string_or_none(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _render_nullable_timestamp(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat().replace("+00:00", "Z") if value is not None else None


def _as_utc(value: datetime) -> datetime:
    """Render one PostgreSQL instant independently of the session time zone."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ImportQualityEvaluationError(
            "IMPORT_QUALITY_AUTHORITY_TIMESTAMP_INVALID",
            "authority timestamps must be timezone-aware",
        )
    return value.astimezone(UTC)


def _minimum_datetime(current: datetime | None, value: datetime) -> datetime:
    return value if current is None or value < current else current


def _maximum_datetime(current: datetime | None, value: datetime) -> datetime:
    return value if current is None or value > current else current


def _minimum_date(current: date | None, value: date) -> date:
    return value if current is None or value < current else current


def _maximum_date(current: date | None, value: date) -> date:
    return value if current is None or value > current else current
