"""Data-owned automatic sync controls; the token is write-only."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import Field, JsonValue
from sqlalchemy import Engine, text
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.tushare import credentials, publication, settings, store
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, EvidenceRecord


class SyncSettingsRequest(ApiModel):
    revision: int
    enabled: bool


class SyncTokenRequest(ApiModel):
    token: str = Field(repr=False, min_length=16, max_length=512)


class SyncStatus(ApiModel):
    settings: dict[str, JsonValue]
    token_configured: bool
    datasets: list[dict[str, JsonValue]]
    progress: list[dict[str, JsonValue]]
    jobs: list[dict[str, JsonValue]]
    unplanned_contracts: int


class SyncEvidence(EvidenceRecord):
    request_id: str


def register(app: FastAPI, access: WorkspaceAccess, engine: Engine) -> None:
    @app.get("/api/sync", response_model=SyncStatus)
    def status() -> dict[str, Any]:
        return settings.status(engine)

    @app.post("/api/sync/settings", response_model=SyncStatus)
    async def configure(request: Request, document: SyncSettingsRequest) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(settings.configure, engine, **document.model_dump())

    @app.post("/api/sync/token", response_model=SyncStatus)
    async def token(request: Request, document: SyncTokenRequest) -> dict[str, Any]:
        access.protect(request)
        await run_in_threadpool(credentials.save, document.token)
        return await run_in_threadpool(settings.status, engine)

    @app.get("/api/sync/jobs/{request_id}", response_model=SyncEvidence)
    def job(request_id: UUID) -> dict[str, Any]:
        return store.job(engine, request_id)

    @app.get("/api/sync/receipts/{request_id}", response_model=SyncEvidence)
    def receipt(request_id: UUID) -> dict[str, Any]:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM data_sync_receipts WHERE receipt_id=:id"),
                    {"id": request_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("已发布下载版本不存在")
        return {
            "request_id": str(request_id),
            **store.serial(row),
            **publication.read_snapshot(row["manifest_hash"], row["manifest_bytes"]),
        }
