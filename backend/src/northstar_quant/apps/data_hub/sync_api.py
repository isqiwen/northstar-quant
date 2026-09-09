"""Data-owned synchronization API; tokens never cross the browser protocol."""

from typing import cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import JsonValue
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.research import ImportSpec
from northstar_quant.data_management.tushare import jobs
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, UUIDText


class SyncRequest(ApiModel):
    request_id: UUIDText
    spec: dict[str, JsonValue]


class SyncJob(ApiModel):
    request_id: str
    request_hash: str
    parameters: dict[str, JsonValue]
    code_revision: str
    status: str
    attempt_id: str | None
    error: str | None
    created_at: str
    updated_at: str


def register(app: FastAPI, access: WorkspaceAccess, engine: Engine) -> None:
    @app.post("/api/sync/tushare", response_model=SyncJob)
    async def submit(request: Request, document: SyncRequest) -> dict[str, object]:
        access.protect(request)
        spec = ImportSpec.from_mapping(cast(dict[str, object], document.spec))
        return await run_in_threadpool(jobs.submit, engine, spec, UUID(document.request_id))

    @app.get("/api/sync", response_model=list[SyncJob])
    def recent() -> list[dict[str, object]]:
        return jobs.recent(engine)

    @app.get("/api/sync/{request_id}", response_model=SyncJob)
    def get(request_id: UUID) -> dict[str, object]:
        return jobs.get(engine, request_id)
