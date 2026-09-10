from __future__ import annotations

from fastapi import FastAPI, Request
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.research.operations import ResearchOperations
from northstar_quant.research.runs import RunStore
from northstar_quant.web.access import (
    WorkspaceAccess,
)
from northstar_quant.web.datasets import DatasetDetails
from northstar_quant.web.requests import ApiModel, EvidenceRecord

from .configuration_api import ResearchConfiguration


class ComparisonRequest(ApiModel):
    run_ids: list[str]


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
    close: str
    cash: str
    position_lots: int
    realized_pnl: str
    unrealized_pnl: str
    total_fees: str
    drawdown: str
    drawdown_fraction: str
    long_lots: int
    short_lots: int
    net_exposure: str
    gross_exposure: str
    settlement_pnl: str
    trade_realized_pnl: str
    terms_id: str | None = None
    margin_used: str | None = None
    available: str | None = None
    reserved_fee: str
    reserved_margin: str
    reserved_close_lots: int
    available_after_reservations: str | None = None


class EvaluationPlan(ApiModel):
    plan_id: str
    revision: str
    snapshot_id: str
    content_hash: str
    window: str
    event_start: str | None
    event_end: str | None
    expected_bars: int | None
    benchmark: str
    annualization: str
    risk_free_rate: str
    sample_use: str


class EvaluationResult(ApiModel):
    plan: EvaluationPlan
    status: str
    observed_bars: int
    benchmark_ending_equity: str
    benchmark_return: str
    excess_return: str
    annualized_return: str | None
    sharpe: str | None
    limitations: list[str]


class ResearchResultDocument(EvidenceRecord):
    evaluation: EvaluationResult
    summary: ResearchSummary
    equity_curve: list[EquityPoint]
    decisions: list[dict[str, JsonValue]]
    fills: list[dict[str, JsonValue]]
    settlements: list[dict[str, JsonValue]]
    orders: list[dict[str, JsonValue]]
    data: DatasetDetails | None


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
