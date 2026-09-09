"""Data-owned read-only processing diagnostics and attempt inspection."""

from dataclasses import asdict
from uuid import UUID

from fastapi import FastAPI
from pydantic import JsonValue
from sqlalchemy import Engine

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.processing_status import processing_status
from northstar_quant.web.requests import ApiModel, EvidenceRecord


class ProcessingAttempt(EvidenceRecord):
    attempt_id: str
    source_id: str
    status: str
    stage: str
    snapshot_id: str | None
    error: str | None
    parameters: dict[str, JsonValue]
    created_at: str


class ProcessingQueueStatus(ApiModel):
    observed_at: str
    total: int
    pending: int
    running: int
    published: int
    failed: int
    oldest_pending_id: str | None
    oldest_pending_at: str | None
    oldest_pending_seconds: int | None


def register(app: FastAPI, engine: Engine, library: DataLibrary) -> None:
    @app.get("/api/processing/status", response_model=ProcessingQueueStatus)
    def queue_status() -> dict[str, object]:
        status = processing_status(engine)
        result = asdict(status)
        result["observed_at"] = status.observed_at.isoformat().replace("+00:00", "Z")
        result["oldest_pending_id"] = (
            str(status.oldest_pending_id) if status.oldest_pending_id else None
        )
        result["oldest_pending_at"] = (
            status.oldest_pending_at.isoformat().replace("+00:00", "Z")
            if status.oldest_pending_at
            else None
        )
        return result

    @app.get(
        "/api/attempts", response_model=list[ProcessingAttempt], response_model_exclude_unset=True
    )
    def list_attempts(limit: int = 50) -> list[dict[str, object]]:
        return library.list_attempts(limit=limit)

    @app.get(
        "/api/attempts/{attempt_id}",
        response_model=ProcessingAttempt,
        response_model_exclude_unset=True,
    )
    def attempt_details(attempt_id: UUID) -> dict[str, object]:
        return library.attempt(attempt_id)
