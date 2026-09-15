"""Fixed physical merge jobs; the Data worker owns their execution."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from pydantic import Field, JsonValue
from sqlalchemy import Engine

from northstar_quant.data_management import compaction
from northstar_quant.web.requests import ApiModel, UUIDText

from .exploration_api import ExplorerRows


class CompactionRequest(ApiModel):
    request_id: UUIDText
    dataset: str = Field(max_length=16)
    scope: str = Field(max_length=40)
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    receipt_ids: list[UUIDText] = Field(min_length=2, max_length=32)


class Compaction(ApiModel):
    compaction_id: str
    plan_id: str
    plan: dict[str, JsonValue]
    created_at: str
    status: str
    result: dict[str, JsonValue] | None
    error: str | None


class CompactionPage(ApiModel):
    offset: int = Field(ge=0, le=20000)
    limit: int = Field(ge=1, le=1000)


def register(app: FastAPI, engine: Engine) -> None:
    @app.post("/api/explorer/compactions", status_code=202, response_model=Compaction)
    def submit(request: Request, document: CompactionRequest) -> dict[str, Any]:
        app.state.workspace_access.protect(request)
        return compaction.submit(
            engine,
            UUID(document.request_id),
            **document.model_dump(exclude={"request_id", "receipt_ids"}),
            receipt_ids=[UUID(v) for v in document.receipt_ids],
        )

    @app.get("/api/explorer/compactions", response_model=list[Compaction])
    def listing() -> list[dict[str, Any]]:
        return compaction.listing(engine)

    @app.get("/api/explorer/compactions/{compaction_id}", response_model=Compaction)
    def get(compaction_id: UUIDText) -> dict[str, Any]:
        return compaction.get(engine, compaction_id)

    @app.post("/api/explorer/compactions/{compaction_id}/query", response_model=ExplorerRows)
    def query(compaction_id: UUIDText, document: CompactionPage) -> dict[str, Any]:
        return compaction.read(engine, compaction_id, **document.model_dump())

    @app.post("/api/explorer/compactions/{compaction_id}/export", response_model=ExplorerRows)
    def export(compaction_id: UUIDText, document: CompactionPage) -> dict[str, Any]:
        result = query(compaction_id, document)
        if not result["export_allowed"]:
            raise HTTPException(403, "来源未开放导出权限")
        return result
