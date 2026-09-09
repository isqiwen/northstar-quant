"""Read-only browsing transport; selection and evidence are owned by Data Management."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import Field, JsonValue
from sqlalchemy import Engine

from northstar_quant.data_management.exploration import catalog, quality, rows
from northstar_quant.web.requests import ApiModel


class ExplorerCatalog(ApiModel):
    datasets: list[dict[str, JsonValue]]
    exchanges: list[str]
    products: list[dict[str, JsonValue]]


class ContractSearch(ApiModel):
    exchange: str = Field(max_length=12)
    product: str = Field(max_length=20)
    search: str = Field(max_length=40)
    offset: int = Field(ge=0, le=100000)


class ExplorerList(ApiModel):
    rows: list[dict[str, JsonValue]]
    total: int


class ExplorerRange(ApiModel):
    dataset: str = Field(max_length=16)
    scope: str = Field(max_length=40)
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    offset: int = Field(ge=0, le=100000)


class ExplorerQuery(ExplorerRange):
    receipt_ids: list[str] = Field(max_length=32)
    limit: int = Field(ge=1, le=1000)


class ExplorerCoverage(ApiModel):
    days: list[dict[str, JsonValue]]
    jobs: list[dict[str, JsonValue]]
    note: str


class ExplorerRows(ApiModel):
    dataset: str
    scope: str
    start: str
    end: str
    receipt_ids: list[str]
    view_id: str
    rows: list[dict[str, JsonValue]]
    total: int
    offset: int
    limit: int
    fields: list[dict[str, JsonValue]]
    versions: list[dict[str, JsonValue]]
    sources: list[dict[str, JsonValue]]
    export_allowed: bool
    note: str


def register(app: FastAPI, engine: Engine) -> None:
    @app.get("/api/explorer", response_model=ExplorerCatalog)
    def overview() -> dict[str, Any]:
        return catalog.overview(engine)

    @app.post("/api/explorer/contracts", response_model=ExplorerList)
    def contracts(document: ContractSearch) -> dict[str, Any]:
        return catalog.contracts(engine, **document.model_dump())

    @app.post("/api/explorer/coverage", response_model=ExplorerCoverage)
    def coverage(document: ExplorerRange) -> dict[str, Any]:
        return quality.coverage(engine, **document.model_dump(exclude={"offset"}))

    @app.post("/api/explorer/versions", response_model=ExplorerList)
    def versions(document: ExplorerRange) -> dict[str, Any]:
        return catalog.versions(engine, **document.model_dump())

    @app.post("/api/explorer/query", response_model=ExplorerRows)
    def query(document: ExplorerQuery) -> dict[str, Any]:
        values = document.model_dump(exclude={"receipt_ids"})
        return rows.read(engine, receipt_ids=[UUID(v) for v in document.receipt_ids], **values)

    @app.post("/api/explorer/export", response_model=ExplorerRows)
    def export(document: ExplorerQuery) -> dict[str, Any]:
        if not document.receipt_ids:
            raise ValueError("导出必须绑定已选定的固定版本")
        result = query(document)
        if not result["export_allowed"]:
            raise HTTPException(403, "来源未开放导出权限；不能通过数据浏览绕过")
        return result
