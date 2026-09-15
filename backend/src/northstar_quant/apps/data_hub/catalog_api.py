"""Standard fixed catalog transport, independent of supplier endpoint names."""

from typing import Any

from fastapi import FastAPI, Request
from pydantic import Field, JsonValue
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.catalog import browsing as catalog
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel


class CatalogSnapshot(ApiModel):
    snapshot_id: str
    exchange: str
    product: str
    contract: str
    series: str
    entity_type: str
    files: list[dict[str, JsonValue]]
    reference: dict[str, JsonValue]
    time_basis: str
    fee_basis: str


class CatalogQuery(ApiModel):
    domain: str = Field(min_length=1, max_length=160)
    contract: str = ""
    series: str = ""
    start: str = ""
    end: str = ""
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


class CatalogRows(ApiModel):
    snapshot_id: str
    domain: str
    rows: list[dict[str, JsonValue]]
    total: int
    offset: int
    limit: int


def register(app: FastAPI, access: WorkspaceAccess, engine: Engine) -> None:
    @app.get("/api/catalog/snapshots/{snapshot_id}", response_model=CatalogSnapshot)
    async def describe(snapshot_id: str) -> dict[str, Any]:
        return await run_in_threadpool(catalog.snapshot, engine, snapshot_id)

    @app.post("/api/catalog/snapshots/{snapshot_id}/query", response_model=CatalogRows)
    async def query(request: Request, snapshot_id: str, document: CatalogQuery) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(catalog.rows, engine, snapshot_id, **document.model_dump())
