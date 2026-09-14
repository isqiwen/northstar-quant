"""Read-only transport for the contract collection workbench."""

from typing import Any

from fastapi import FastAPI, Request
from pydantic import Field, JsonValue
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.contract_data.collection_query import search
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel


class CollectionQuery(ApiModel):
    exchange: str = Field(max_length=20)
    product: str = Field(max_length=40)
    search: str = Field(max_length=100)
    status: str = Field(max_length=20)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class CollectionPage(ApiModel):
    total: int
    offset: int
    limit: int
    items: list[dict[str, JsonValue]]
    exchanges: list[str]
    products: list[str]


def register(app: FastAPI, access: WorkspaceAccess, engine: Engine) -> None:
    @app.post("/api/sync/contracts/query", response_model=CollectionPage)
    async def query(request: Request, document: CollectionQuery) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(search, engine, **document.model_dump())
