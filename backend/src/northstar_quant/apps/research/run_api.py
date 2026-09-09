from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.operations import ResearchOperations
from northstar_quant.research.runs import RunStore
from northstar_quant.web.access import (
    WorkspaceAccess,
)
from northstar_quant.web.requests import ApiModel, EvidenceRecord, UUIDText, _object

from .configuration_api import ResearchConfiguration, ResearchConfigurationInput


class RunRequest(ApiModel):
    snapshot_id: UUIDText
    config: ResearchConfigurationInput


class ComparisonRequest(ApiModel):
    run_ids: list[str]


class RunCreated(ApiModel):
    run_id: str
    url: str


class SnapshotReference(EvidenceRecord):
    id: str
    content_hash: str


class ResearchSummary(ApiModel):
    bar_count: int
    decision_count: int
    fill_count: int
    initial_cash: str
    ending_cash: str
    ending_position_lots: int
    realized_pnl: str
    unrealized_pnl: str
    total_fees: str
    ending_equity: str
    total_return: str
    max_drawdown: str
    max_drawdown_fraction: str


class RunSummary(EvidenceRecord):
    run_id: str
    created_at: str
    committed_code: bool
    code_revision: str
    snapshot: SnapshotReference
    config: ResearchConfiguration
    summary: ResearchSummary
    market: dict[str, JsonValue]


class EquityPoint(EvidenceRecord):
    at: str
    equity: str
    observation_id: str


class ResearchResultDocument(EvidenceRecord):
    summary: ResearchSummary
    equity_curve: list[EquityPoint]
    decisions: list[dict[str, JsonValue]]
    fills: list[dict[str, JsonValue]]
    data: dict[str, JsonValue] | None


class RunDetail(EvidenceRecord):
    run_id: str
    created_at: str
    committed_code: bool
    code_revision: str
    snapshot: SnapshotReference
    config: ResearchConfiguration
    result: ResearchResultDocument


class ResearchAttempt(EvidenceRecord):
    attempt_id: str
    status: str
    created_at: str
    run_id: str | None
    error: str | None


class Comparison(ResearchSummary):
    run_id: str
    strategy: str


def register(
    app: FastAPI, access: WorkspaceAccess, library: DatasetReader, store: RunStore
) -> None:
    operations = ResearchOperations(library, store)

    @app.get("/api/runs", response_model=list[RunSummary], response_model_exclude_unset=True)
    def list_runs(limit: int = 50) -> list[dict[str, object]]:
        return store.list(limit=limit)

    @app.get("/api/runs/{run_id}", response_model=RunDetail, response_model_exclude_unset=True)
    def get_run(run_id: str) -> dict[str, object]:
        return store.get(run_id)

    @app.post(
        "/api/runs", status_code=201, response_model=RunCreated, response_model_exclude_unset=True
    )
    async def submit(request: Request, document: RunRequest) -> dict[str, str]:
        access.protect(request)
        payload = document.model_dump(mode="json", exclude_unset=True)
        snapshot_text = payload["snapshot_id"]
        if not isinstance(snapshot_text, str):
            raise ValueError("snapshot_id 必须是规范的 UUID。")
        snapshot_id = UUID(snapshot_text)
        if str(snapshot_id) != snapshot_text:
            raise ValueError("snapshot_id 必须是规范的 UUID。")
        configuration = ResearchConfig.from_mapping(_object(payload["config"]))

        run_id = await run_in_threadpool(operations.run, snapshot_id, configuration)
        return {"run_id": run_id, "url": f"/runs/{run_id}"}

    @app.get(
        "/api/research-attempts",
        response_model=list[ResearchAttempt],
        response_model_exclude_unset=True,
    )
    def attempts() -> list[dict[str, object]]:
        return store.attempts()

    @app.post(
        "/api/run-comparisons", response_model=list[Comparison], response_model_exclude_unset=True
    )
    async def compare(request: Request, document: ComparisonRequest) -> list[dict[str, object]]:
        access.protect(request)
        ids = document.run_ids
        return await run_in_threadpool(operations.compare, ids)
