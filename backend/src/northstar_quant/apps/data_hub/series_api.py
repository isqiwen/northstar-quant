"""Authenticated research-series discovery, interval history and publication retry."""

from typing import Any

from fastapi import FastAPI, Request
from pydantic import Field, JsonValue
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.series_data import catalog
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel


class SeriesQuery(ApiModel):
    dataset: str = ""
    search: str = ""
    offset: int = Field(default=0, ge=0)


class SeriesVersions(ApiModel):
    dataset: str
    scope: str
    offset: int = Field(default=0, ge=0)


class SeriesIdentity(ApiModel):
    dataset: str
    scope: str


class SeriesRows(ApiModel):
    rows: list[dict[str, JsonValue]]
    total: int


class SeriesRetry(ApiModel):
    retried: int


def register(app: FastAPI, access: WorkspaceAccess, engine: Engine) -> None:
    @app.post("/api/series/query", response_model=SeriesRows)
    async def query(request: Request, document: SeriesQuery) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(catalog.query, engine, **document.model_dump())

    @app.post("/api/series/versions", response_model=SeriesRows)
    async def versions(request: Request, document: SeriesVersions) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(catalog.versions, engine, **document.model_dump())

    @app.post("/api/series/retry", response_model=SeriesRetry)
    async def retry(request: Request, document: SeriesIdentity) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(catalog.retry, engine, **document.model_dump())
