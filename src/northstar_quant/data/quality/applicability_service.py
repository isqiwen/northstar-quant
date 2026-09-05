"""Read-only applicability checks for immutable series-window evidence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import QualityEvaluation
from northstar_quant.data.quality.evaluations import (
    DAILY_QUALITY_EVALUATION_SCOPE,
    DAILY_QUALITY_RULE_SET_NAME,
    DAILY_QUALITY_RULE_SET_VERSION,
    MINUTE_QUALITY_EVALUATION_SCOPE,
    MINUTE_QUALITY_RULE_SET_NAME,
    MINUTE_QUALITY_RULE_SET_VERSION,
    DailyQualityEvaluationError,
    MinuteQualityEvaluationError,
    QualityApplicabilityError,
    QualityApplicabilityResult,
)
from northstar_quant.data.quality.minute_service import current_minute_quality_input_fingerprint
from northstar_quant.data.quality.service import current_daily_quality_input_fingerprint


class QualityApplicabilityService:
    """Check whether a stored daily/minute conclusion still has its exact inputs.

    This service never updates an older evaluation, generates a job, or follows
    a current source-admission review.  It only compares the original immutable
    fingerprint to one current, repeatable committed database view.  An
    inapplicable conclusion stays historical; an operator must append a new
    evaluation using the corresponding manual command.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def assess(self, quality_evaluation_id: UUID) -> QualityApplicabilityResult:
        """Return a fail-closed applicability result without persisting anything."""

        if (
            self._session.in_transaction()
            or self._session.new
            or self._session.dirty
            or self._session.deleted
        ):
            raise QualityApplicabilityError(
                "QUALITY_APPLICABILITY_REQUIRES_CLEAN_SESSION",
                "the quality applicability check requires a clean, idle dedicated database session",
            )
        self._session.rollback()
        self._begin_consistent_snapshot()
        try:
            evaluation = self._session.scalar(
                select(QualityEvaluation).where(QualityEvaluation.id == quality_evaluation_id)
            )
            if evaluation is None:
                raise QualityApplicabilityError(
                    "QUALITY_EVALUATION_NOT_FOUND",
                    "the requested quality evaluation does not exist",
                )
            try:
                current_fingerprint = _current_fingerprint(self._session, evaluation)
            except (DailyQualityEvaluationError, MinuteQualityEvaluationError):
                return _blocked_result(evaluation, "QUALITY_EVALUATION_INPUT_UNINTERPRETABLE")
            if current_fingerprint != evaluation.input_fingerprint:
                return _blocked_result(evaluation, "REVALIDATION_REQUIRED")
            return QualityApplicabilityResult(
                quality_evaluation_id=evaluation.id,
                rule_set_name=evaluation.rule_set_name,
                rule_set_version=evaluation.rule_set_version,
                evaluation_scope=evaluation.evaluation_scope,
                applicable=True,
                delivery_gate=evaluation.delivery_gate,
                reason_code="QUALITY_EVALUATION_INPUT_CURRENT",
            )
        finally:
            self._session.rollback()

    def _begin_consistent_snapshot(self) -> None:
        self._session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))


def _current_fingerprint(session: Session, evaluation: QualityEvaluation) -> str:
    if (
        evaluation.evaluation_scope == DAILY_QUALITY_EVALUATION_SCOPE
        and evaluation.rule_set_name == DAILY_QUALITY_RULE_SET_NAME
        and evaluation.rule_set_version == DAILY_QUALITY_RULE_SET_VERSION
    ):
        return current_daily_quality_input_fingerprint(session, evaluation)
    if (
        evaluation.evaluation_scope == MINUTE_QUALITY_EVALUATION_SCOPE
        and evaluation.rule_set_name == MINUTE_QUALITY_RULE_SET_NAME
        and evaluation.rule_set_version == MINUTE_QUALITY_RULE_SET_VERSION
    ):
        return current_minute_quality_input_fingerprint(session, evaluation)
    raise QualityApplicabilityError(
        "QUALITY_EVALUATION_RULE_SET_UNSUPPORTED",
        "the requested quality evaluation does not use a supported series-window rule set",
    )


def _blocked_result(evaluation: QualityEvaluation, reason_code: str) -> QualityApplicabilityResult:
    return QualityApplicabilityResult(
        quality_evaluation_id=evaluation.id,
        rule_set_name=evaluation.rule_set_name,
        rule_set_version=evaluation.rule_set_version,
        evaluation_scope=evaluation.evaluation_scope,
        applicable=False,
        delivery_gate="BLOCKED",
        reason_code=reason_code,
    )
