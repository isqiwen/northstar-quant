"""Shared reads and classifications for persisted import-quality evidence."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from northstar_quant.data_management.catalog.models import ImportRun
from northstar_quant.data_management.quality.evaluations import (
    ImportQualityEvaluationError,
)

SAFE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


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
