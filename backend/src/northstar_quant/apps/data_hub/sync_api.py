"""Data-owned automatic sync controls; the token is write-only."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import Field, JsonValue
from sqlalchemy import Engine, text
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.tushare import (
    credentials,
    job_query,
    publication,
    reprocessing,
    settings,
    store,
)
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, EvidenceRecord


class ContractReviewRequest(ApiModel):
    scope: str = Field(min_length=1, max_length=40)


class ContractReview(ApiModel):
    scope: str
    display_name: str
    exchange: str
    product: str
    listing_date: str | None
    delisting_date: str | None
    required_end: str | None
    status: str
    admitted: bool
    requirements: list[dict[str, JsonValue]]
    reasons: list[str]
    policy: str


class SyncSettingsRequest(ApiModel):
    revision: int
    enabled: bool


class SyncReprocessRequest(ApiModel):
    request_id: str
    source_generation: str


class SyncTokenRequest(ApiModel):
    token: str = Field(repr=False, min_length=16, max_length=512)


class SyncLane(ApiModel):
    lane: str
    start: str
    end: str
    total: int
    validated: int
    waiting: int
    blocked: int
    running: int
    oldest_pending: str | None


class SyncStatus(ApiModel):
    settings: dict[str, JsonValue]
    token_configured: bool
    datasets: list[dict[str, JsonValue]]
    progress: list[dict[str, JsonValue]]
    jobs: list[dict[str, JsonValue]]
    unplanned_contracts: int
    lanes: list[SyncLane]


class SyncJobQuery(ApiModel):
    dataset: str
    status: str
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class SyncJobPage(ApiModel):
    total: int
    offset: int
    limit: int
    items: list[dict[str, JsonValue]]


class SyncEvidence(EvidenceRecord):
    request_id: str


def register(app: FastAPI, access: WorkspaceAccess, engine: Engine, library: DataLibrary) -> None:
    @app.post("/api/sync/contracts/review", response_model=ContractReview)
    async def review_contract(request: Request, document: ContractReviewRequest) -> dict[str, Any]:
        from northstar_quant.data_management.tushare.contract_review import review

        access.protect(request)
        return await run_in_threadpool(review, engine, document.scope)

    @app.get("/api/sync", response_model=SyncStatus)
    def status() -> dict[str, Any]:
        result = settings.status(engine)
        result["settings"]["source_capacity"] = library.storage_capacity()
        return result

    @app.post("/api/sync/settings", response_model=SyncStatus)
    async def configure(request: Request, document: SyncSettingsRequest) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(settings.configure, engine, **document.model_dump())

    @app.post("/api/sync/reprocess", response_model=SyncEvidence)
    async def reprocess(request: Request, document: SyncReprocessRequest) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(
            reprocessing.enqueue,
            engine,
            request_id=UUID(document.request_id),
            source_generation=UUID(document.source_generation),
        )

    @app.post("/api/sync/token", response_model=SyncStatus)
    async def token(request: Request, document: SyncTokenRequest) -> dict[str, Any]:
        access.protect(request)
        await run_in_threadpool(credentials.save, document.token)
        return await run_in_threadpool(settings.status, engine)

    @app.post("/api/sync/jobs/query", response_model=SyncJobPage)
    async def search_jobs(request: Request, document: SyncJobQuery) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(job_query.search, engine, **document.model_dump())

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
