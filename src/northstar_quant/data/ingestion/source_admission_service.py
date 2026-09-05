"""Local persistence for bounded SHFE daily source-admission evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    SHFE_DAILY_ACQUISITION_USE,
    SHFE_DAILY_ADAPTER_NAME,
    SHFE_DAILY_ADAPTER_VERSION,
    SHFE_DAILY_AVAILABLE_AT_BASIS,
    SHFE_DAILY_ENDPOINT_ID,
    SHFE_DAILY_MAPPING_VERSION,
    SHFE_DAILY_REDISTRIBUTION_POLICY,
    SHFE_DAILY_RETENTION_POLICY,
    SHFE_DAILY_SOURCE_NAME,
    ShfeDailySourceAdmissionReview,
)
from northstar_quant.data.ingestion.source_admission import (
    RecordShfeDailySourceAdmissionReviewCommand,
    ShfeDailySourceAdmissionReviewResult,
    SourceAdmissionReviewError,
    validate_record_shfe_daily_source_admission_review_command,
)

_SHFE_DAILY_SOURCE_ADMISSION_LOCK_CLASS = 0x514448
_SHFE_DAILY_SOURCE_ADMISSION_LOCK_OBJECT = 1


def acquire_shfe_daily_source_admission_review_lock(session: Session) -> None:
    """Start a read-committed transaction and serialize admission state.

    The source-admission table intentionally has no mutable ``current`` flag. A
    PostgreSQL transaction-level advisory lock keeps a newly recorded ``BLOCKED``
    or ``RESTRICTED`` conclusion from racing between another command's
    latest-review check and its retrieval reservation. The critical transaction
    is explicitly set to ``READ COMMITTED`` *before* taking the lock. That makes
    the post-lock review query see a conclusion committed while the lock request
    waited, even if a deployment's connection default is stronger.

    Callers must end any earlier read transaction before calling this function.
    ``record`` and ``retrieve`` do so immediately before the critical path.
    """

    # ``SET TRANSACTION`` must be the first SQL statement in the critical
    # transaction. It deliberately overrides a connection default such as
    # REPEATABLE READ or SERIALIZABLE: advisory-lock waiting must not pin a
    # snapshot before the latest-review query runs.
    session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_class, :lock_object)"),
        {
            "lock_class": _SHFE_DAILY_SOURCE_ADMISSION_LOCK_CLASS,
            "lock_object": _SHFE_DAILY_SOURCE_ADMISSION_LOCK_OBJECT,
        },
    )


class ShfeDailySourceAdmissionReviewService:
    """Record one immutable, locally entered SHFE daily review conclusion.

    The service has no HTTP client and no provider request capability.  It only
    writes the bounded review record needed by the later retrieval gate.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        command: RecordShfeDailySourceAdmissionReviewCommand,
    ) -> ShfeDailySourceAdmissionReviewResult:
        """Persist or replay one exact accountable source-admission conclusion."""

        command = validate_record_shfe_daily_source_admission_review_command(command)
        existing = self._session.scalar(
            select(ShfeDailySourceAdmissionReview).where(
                ShfeDailySourceAdmissionReview.idempotency_key == command.idempotency_key
            )
        )
        if existing is not None:
            return self._replay_or_reject(existing, command)
        # End the fast-path read transaction before starting the dedicated
        # read-committed advisory-lock admission transaction.
        self._session.rollback()
        acquire_shfe_daily_source_admission_review_lock(self._session)
        try:
            # A concurrent recorder might have won the advisory lock between
            # the initial idempotency lookup and this transaction's lock
            # acquisition. Re-read under the lock before deciding whether this
            # is a new record.
            existing = self._session.scalar(
                select(ShfeDailySourceAdmissionReview).where(
                    ShfeDailySourceAdmissionReview.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                return self._replay_or_reject(existing, command)
            if command.valid_until <= _utc_now():
                raise SourceAdmissionReviewError(
                    "INVALID_SOURCE_ADMISSION_REVIEW_VALIDITY",
                    "valid_until must be after the current UTC time",
                )
            review_sequence = self._next_review_sequence()

            review = ShfeDailySourceAdmissionReview(
                review_sequence=review_sequence,
                source_name=SHFE_DAILY_SOURCE_NAME,
                adapter_name=SHFE_DAILY_ADAPTER_NAME,
                adapter_version=SHFE_DAILY_ADAPTER_VERSION,
                mapping_version=SHFE_DAILY_MAPPING_VERSION,
                endpoint_id=SHFE_DAILY_ENDPOINT_ID,
                status=command.status,
                acquisition_use=SHFE_DAILY_ACQUISITION_USE,
                retention_policy=SHFE_DAILY_RETENTION_POLICY,
                redistribution_policy=SHFE_DAILY_REDISTRIBUTION_POLICY,
                available_at_basis=SHFE_DAILY_AVAILABLE_AT_BASIS,
                evidence_ref=command.evidence_ref,
                evidence_sha256=command.evidence_sha256,
                reviewer_id=command.reviewer_id,
                valid_until=command.valid_until,
                idempotency_key=command.idempotency_key,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            self._session.add(review)
            try:
                self._session.commit()
            except IntegrityError as error:
                self._session.rollback()
                concurrent = self._session.scalar(
                    select(ShfeDailySourceAdmissionReview).where(
                        ShfeDailySourceAdmissionReview.idempotency_key == command.idempotency_key
                    )
                )
                if concurrent is not None:
                    return self._replay_or_reject(concurrent, command)
                raise SourceAdmissionReviewError(
                    "SOURCE_ADMISSION_REVIEW_RESERVATION_CONFLICT",
                    (
                        "the source-admission review reservation conflicted; "
                        "retry the same command safely"
                    ),
                ) from error
            return _result_from_review(review, replayed=False)
        except BaseException:
            # See ``acquire_shfe_daily_source_admission_review_lock``: an
            # invalid new record must not leave a transaction-level lock held
            # for a long-lived direct caller.
            self._session.rollback()
            raise

    def _next_review_sequence(self) -> int:
        """Allocate the one total review order inside the held admission lock.

        ``created_at`` is audit time, not current-conclusion order: PostgreSQL's
        transaction timestamp can predate an advisory-lock wait. The fixed
        lock serializes all supported writers, so ``max + 1`` is a monotonic,
        portable order for the review record that actually acquired the lock.
        The database's unique/positive constraints detect malformed or
        colliding orders; the documented database-role boundary remains
        necessary to prevent unauthorized direct review writes.
        """

        next_sequence = self._session.scalar(
            select(func.coalesce(func.max(ShfeDailySourceAdmissionReview.review_sequence), 0) + 1)
        )
        if not isinstance(next_sequence, int):  # pragma: no cover - database invariant
            raise SourceAdmissionReviewError(
                "SOURCE_ADMISSION_REVIEW_SEQUENCE_UNAVAILABLE",
                "the source-admission review order could not be allocated",
            )
        return next_sequence

    def _replay_or_reject(
        self,
        review: ShfeDailySourceAdmissionReview,
        command: RecordShfeDailySourceAdmissionReviewCommand,
    ) -> ShfeDailySourceAdmissionReviewResult:
        """Treat a repeated key as a replay only when all durable intent matches."""

        if not _review_matches_command(review, command):
            self._session.rollback()
            raise SourceAdmissionReviewError(
                "IDEMPOTENCY_KEY_REUSED",
                (
                    "the source-admission review idempotency key was already used "
                    "for a different intent"
                ),
            )
        result = _result_from_review(review, replayed=True)
        # This is a read-only replay.  Explicitly end the transaction before the
        # command returns so callers do not retain an unnecessary DB transaction.
        self._session.rollback()
        return result


def _review_matches_command(
    review: ShfeDailySourceAdmissionReview,
    command: RecordShfeDailySourceAdmissionReviewCommand,
) -> bool:
    """Compare every mutable-intent field as well as the fixed SHFE scope."""

    return (
        review.source_name == SHFE_DAILY_SOURCE_NAME
        and review.adapter_name == SHFE_DAILY_ADAPTER_NAME
        and review.adapter_version == SHFE_DAILY_ADAPTER_VERSION
        and review.mapping_version == SHFE_DAILY_MAPPING_VERSION
        and review.endpoint_id == SHFE_DAILY_ENDPOINT_ID
        and review.status == command.status
        and review.acquisition_use == SHFE_DAILY_ACQUISITION_USE
        and review.retention_policy == SHFE_DAILY_RETENTION_POLICY
        and review.redistribution_policy == SHFE_DAILY_REDISTRIBUTION_POLICY
        and review.available_at_basis == SHFE_DAILY_AVAILABLE_AT_BASIS
        and review.evidence_ref == command.evidence_ref
        and review.evidence_sha256 == command.evidence_sha256
        and review.reviewer_id == command.reviewer_id
        and _as_utc(review.valid_until) == command.valid_until
        and review.correlation_id == command.correlation_id
        and review.causation_id == command.causation_id
    )


def _result_from_review(
    review: ShfeDailySourceAdmissionReview,
    *,
    replayed: bool,
) -> ShfeDailySourceAdmissionReviewResult:
    return ShfeDailySourceAdmissionReviewResult(
        source_admission_review_id=review.id,
        status=review.status,
        valid_until=_as_utc(review.valid_until),
        replayed=replayed,
    )


def _as_utc(value: datetime) -> datetime:
    """Normalize one PostgreSQL authority timestamp to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceAdmissionReviewError(
            "INVALID_SOURCE_ADMISSION_REVIEW_TIMESTAMP",
            "stored source-admission timestamps must be timezone-aware",
        )
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    """Provide one UTC clock source for admission-record validity checks."""

    return datetime.now(UTC)
