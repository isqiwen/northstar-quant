"""Shared reads and classifications for persisted import-quality evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from northstar_quant.data.catalog.models import ImportRun, ProviderRetrieval
from northstar_quant.data.ingestion.providers.shfe import ShfeDailyJsonAdapter
from northstar_quant.data.quality.evaluations import (
    MAX_IMPORT_QUALITY_PROVIDER_RETRIEVALS,
    ImportQualityEvaluationError,
)

SAFE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_PROVIDER_IMPORT_IDEMPOTENCY_PREFIX = "provider-import-"


@dataclass(frozen=True)
class ProviderRetrievalEvidence:
    """Provider facts plus the expected inner-import reconciliation link."""

    retrievals: tuple[ProviderRetrieval, ...]
    expected_retrieval_id: UUID | None = None
    reconciliation_reason: str | None = None


def load_terminal_import_run(session: Session, import_run_id: UUID) -> ImportRun:
    """Load a terminal import with the receipt and job facts used by quality."""

    import_run = session.scalar(
        select(ImportRun)
        .where(ImportRun.id == import_run_id)
        .options(joinedload(ImportRun.source_receipt), joinedload(ImportRun.job_run))
    )
    if import_run is None:
        raise ImportQualityEvaluationError(
            "IMPORT_QUALITY_IMPORT_RUN_NOT_FOUND",
            "the requested import run does not exist",
        )
    if import_run.status not in {"SUCCEEDED", "FAILED", "QUARANTINED"}:
        raise ImportQualityEvaluationError(
            "IMPORT_NOT_TERMINAL",
            "import-quality evaluation requires a terminal import run",
        )
    if import_run.effect is None:
        raise ImportQualityEvaluationError(
            "IMPORT_TERMINAL_EFFECT_UNKNOWN",
            "the terminal import run does not contain a durable effect",
        )
    return import_run


def load_provider_retrieval_evidence(
    session: Session, import_run: ImportRun
) -> ProviderRetrievalEvidence:
    """Read linked provider evidence and detect an unreconciled inner import."""

    retrievals = tuple(
        session.scalars(
            select(ProviderRetrieval)
            .where(ProviderRetrieval.import_run_id == import_run.id)
            .order_by(ProviderRetrieval.created_at, ProviderRetrieval.id)
            .limit(MAX_IMPORT_QUALITY_PROVIDER_RETRIEVALS + 1)
        ).all()
    )
    if len(retrievals) > MAX_IMPORT_QUALITY_PROVIDER_RETRIEVALS:
        _raise_input_too_large("provider retrievals", MAX_IMPORT_QUALITY_PROVIDER_RETRIEVALS)

    job_run = import_run.job_run
    if job_run is None or job_run.job_kind != ShfeDailyJsonAdapter.job_kind:
        return ProviderRetrievalEvidence(retrievals=retrievals)

    expected_retrieval_id = _provider_retrieval_id_from_inner_job(job_run.idempotency_key)
    if expected_retrieval_id is None:
        return ProviderRetrievalEvidence(
            retrievals=retrievals,
            reconciliation_reason="provider_inner_import_identity_invalid",
        )

    retrieval = session.get(ProviderRetrieval, expected_retrieval_id)
    if retrieval is None:
        return ProviderRetrievalEvidence(
            retrievals=retrievals,
            expected_retrieval_id=expected_retrieval_id,
            reconciliation_reason="provider_inner_import_parent_missing",
        )

    if all(item.id != retrieval.id for item in retrievals):
        if len(retrievals) >= MAX_IMPORT_QUALITY_PROVIDER_RETRIEVALS:
            _raise_input_too_large("provider retrievals", MAX_IMPORT_QUALITY_PROVIDER_RETRIEVALS)
        retrievals = retrievals + (retrieval,)

    if retrieval.import_run_id != import_run.id:
        return ProviderRetrievalEvidence(
            retrievals=retrievals,
            expected_retrieval_id=expected_retrieval_id,
            reconciliation_reason="provider_inner_import_not_reconciled",
        )
    return ProviderRetrievalEvidence(
        retrievals=retrievals,
        expected_retrieval_id=expected_retrieval_id,
    )


def terminal_error_category(error_code: str | None) -> str:
    """Map bounded ingestion errors into stable aggregate quality categories."""

    normalized = error_code if error_code and SAFE_ERROR_CODE.fullmatch(error_code) else "UNKNOWN"
    if normalized in {"CANONICAL_BAR_CONFLICT", "CANONICAL_BAR_CONCURRENT_CONFLICT"}:
        return "CANONICAL_CONFLICT"
    if normalized in {
        "INVALID_OHLC",
        "NEGATIVE_VOLUME",
        "NEGATIVE_TURNOVER",
        "NEGATIVE_OPEN_INTEREST",
    }:
        return "NUMERIC_OR_OHLC"
    if any(
        marker in normalized
        for marker in ("CSV", "PARQUET", "HEADER", "FIELD", "DECIMAL", "ENCODING", "INPUT")
    ):
        return "SCHEMA_OR_PARSE"
    if any(
        marker in normalized
        for marker in ("TIME", "TIMESTAMP", "TRADING_DAY", "SESSION", "CALENDAR", "MINUTE")
    ):
        return "TIME_OR_CALENDAR"
    if any(marker in normalized for marker in ("SYMBOL", "SERIES", "SOURCE_RECORD", "IDENTITY")):
        return "IDENTITY"
    return "REJECTED"


def _provider_retrieval_id_from_inner_job(idempotency_key: str) -> UUID | None:
    if not idempotency_key.startswith(_PROVIDER_IMPORT_IDEMPOTENCY_PREFIX):
        return None
    rendered = idempotency_key.removeprefix(_PROVIDER_IMPORT_IDEMPOTENCY_PREFIX)
    try:
        retrieval_id = UUID(rendered)
    except ValueError:
        return None
    return retrieval_id if str(retrieval_id) == rendered else None


def _raise_input_too_large(evidence_kind: str, maximum: int) -> None:
    raise ImportQualityEvaluationError(
        "IMPORT_QUALITY_EVALUATION_INPUT_TOO_LARGE",
        f"import-quality evaluation accepts at most {maximum} {evidence_kind}",
    )
