"""Read-only applicability checks for immutable import-quality evidence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import ImportQualityEvaluation
from northstar_quant.data.quality.evaluations import (
    IMPORT_QUALITY_RULE_SET_NAME,
    IMPORT_QUALITY_RULE_SET_VERSION,
    MAX_IMPORT_QUALITY_RECORDS,
    ImportQualityApplicabilityError,
    ImportQualityApplicabilityResult,
    ImportQualityCurrentState,
    ImportQualityEvaluationError,
)
from northstar_quant.data.quality.import_service import current_import_quality_state

_OUT_OF_RANGE_CODES = frozenset(
    {
        "IMPORT_QUALITY_EVALUATION_INPUT_TOO_LARGE",
    }
)


class ImportQualityApplicabilityService:
    """Determine whether one explicitly named import-quality conclusion still applies.

    The service never writes import, quality, or snapshot facts, follows a mutable latest pointer,
    or selects another conclusion. Its only operation is to compare a named
    immutable evaluation with the current durable evidence in one dedicated
    repeatable-read view.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def assess(self, import_quality_evaluation_id: UUID) -> ImportQualityApplicabilityResult:
        """Return a fail-closed, read-only result for the specified evaluation."""

        self._require_clean_idle_session()
        self._session.rollback()
        self._begin_consistent_snapshot()
        try:
            evaluation = self._session.get(ImportQualityEvaluation, import_quality_evaluation_id)
            if evaluation is None:
                raise ImportQualityApplicabilityError(
                    "IMPORT_QUALITY_EVALUATION_NOT_FOUND",
                    "the requested import-quality evaluation does not exist",
                )
            if evaluation.rule_set_name != IMPORT_QUALITY_RULE_SET_NAME:
                return _blocked_result(evaluation, "IMPORT_QUALITY_RULE_SET_UNSUPPORTED")
            if not 0 <= evaluation.record_count <= MAX_IMPORT_QUALITY_RECORDS:
                return _blocked_result(evaluation, "IMPORT_QUALITY_INPUT_OUT_OF_RANGE")
            if evaluation.rule_set_version != IMPORT_QUALITY_RULE_SET_VERSION:
                return _blocked_result(evaluation, "IMPORT_QUALITY_RULE_VERSION_MISMATCH")
            try:
                current = current_import_quality_state_for_evaluation(self._session, evaluation)
            except ImportQualityEvaluationError as error:
                return _blocked_result(evaluation, _reason_code_for_error(error))
            if current.input_fingerprint != evaluation.input_fingerprint:
                return _blocked_result(evaluation, "IMPORT_QUALITY_REVALIDATION_REQUIRED")
            if not _state_matches_evaluation(current, evaluation):
                return _blocked_result(evaluation, "IMPORT_QUALITY_APPLICABILITY_DRIFT")
            return ImportQualityApplicabilityResult(
                import_quality_evaluation_id=evaluation.id,
                import_run_id=evaluation.import_run_id,
                rule_set_name=evaluation.rule_set_name,
                rule_set_version=evaluation.rule_set_version,
                applicable=True,
                delivery_gate=evaluation.delivery_gate,
                reason_code="IMPORT_QUALITY_INPUT_CURRENT",
            )
        finally:
            self._session.rollback()

    def _require_clean_idle_session(self) -> None:
        bind = self._session.get_bind()
        if (
            self._session.in_transaction()
            or self._session.new
            or self._session.dirty
            or self._session.deleted
            or (isinstance(bind, Connection) and bind.in_transaction())
        ):
            raise ImportQualityApplicabilityError(
                "IMPORT_QUALITY_APPLICABILITY_REQUIRES_CLEAN_SESSION",
                (
                    "the import-quality applicability check requires a clean, "
                    "idle dedicated database session"
                ),
            )

    def _begin_consistent_snapshot(self) -> None:
        self._session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))


def current_import_quality_state_for_evaluation(
    session: Session, evaluation: ImportQualityEvaluation
) -> ImportQualityCurrentState:
    """Recompute exactly the stored current protocol in the caller's view.

    This has no transaction-management or write behavior. Snapshot publication and
    the named-evidence applicability service share it so both use the same
    version selection and bounded fingerprint implementation.
    """

    if evaluation.rule_set_name != IMPORT_QUALITY_RULE_SET_NAME:
        raise ImportQualityEvaluationError(
            "IMPORT_QUALITY_RULE_SET_UNSUPPORTED",
            "the import-quality evaluation does not use the supported rule set",
        )
    if evaluation.rule_set_version != IMPORT_QUALITY_RULE_SET_VERSION:
        raise ImportQualityEvaluationError(
            "IMPORT_QUALITY_RULE_VERSION_UNSUPPORTED",
            "the import-quality evaluation does not use the current rule version",
        )
    return current_import_quality_state(session, evaluation.import_run_id)


def _state_matches_evaluation(
    current: ImportQualityCurrentState, evaluation: ImportQualityEvaluation
) -> bool:
    """Require every conclusion-bearing aggregate to reproduce exactly."""

    return (
        current.observed_status == evaluation.observed_status
        and current.observed_effect == evaluation.observed_effect
        and current.outcome == evaluation.outcome
        and current.delivery_gate == evaluation.delivery_gate
        and current.rows_read == evaluation.rows_read
        and current.rows_accepted == evaluation.rows_accepted
        and current.rows_rejected == evaluation.rows_rejected
        and current.rows_inserted == evaluation.rows_inserted
        and current.rows_duplicate_identical == evaluation.rows_duplicate_identical
        and current.rows_conflicted == evaluation.rows_conflicted
        and current.record_count == evaluation.record_count
        and current.finding_count == evaluation.finding_count
    )


def _reason_code_for_error(error: ImportQualityEvaluationError) -> str:
    if error.code in _OUT_OF_RANGE_CODES:
        return "IMPORT_QUALITY_INPUT_OUT_OF_RANGE"
    return "IMPORT_QUALITY_INPUT_UNINTERPRETABLE"


def _blocked_result(
    evaluation: ImportQualityEvaluation, reason_code: str
) -> ImportQualityApplicabilityResult:
    return ImportQualityApplicabilityResult(
        import_quality_evaluation_id=evaluation.id,
        import_run_id=evaluation.import_run_id,
        rule_set_name=evaluation.rule_set_name,
        rule_set_version=evaluation.rule_set_version,
        applicable=False,
        delivery_gate="BLOCKED",
        reason_code=reason_code,
    )
