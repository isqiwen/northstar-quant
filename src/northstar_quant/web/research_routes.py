from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from northstar_quant.data.library import DataLibrary
from northstar_quant.research import ResearchConfig, run_research
from northstar_quant.runs import RunStore
from northstar_quant.web.data_views import _lineage_panel
from northstar_quant.web.html import workspace_page
from northstar_quant.web.requests import _object, _read_object
from northstar_quant.web.research_views import _report, _workspace
from northstar_quant.web_access import (
    WorkspaceAccess,
)


def register(app: FastAPI, access: WorkspaceAccess, library: DataLibrary, store: RunStore) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, dataset: UUID | None = None) -> HTMLResponse:
        def workspace() -> str:
            datasets = [item.to_dict() for item in library.list_datasets()]
            selected = None if dataset is None else library.describe_dataset(dataset).to_dict()
            if selected is not None and all(
                item["snapshot_id"] != str(dataset) for item in datasets
            ):
                datasets.append(selected)
            return _workspace(store.list(), datasets, selected)

        return workspace_page(access, request, "研究工作台", await run_in_threadpool(workspace))

    @app.get("/api/runs")
    def list_runs(limit: int = 50) -> list[dict[str, object]]:
        return store.list(limit=limit)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        return store.get(run_id)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def show_run(request: Request, run_id: str) -> HTMLResponse:
        run = await run_in_threadpool(store.get, run_id)
        snapshot_id = UUID(str(_object(run["snapshot"])["id"]))
        lineage = await run_in_threadpool(library.lineage, snapshot_id)
        return workspace_page(access, request, "研究结果", _report(run) + _lineage_panel(lineage))

    @app.post("/api/runs", status_code=201)
    async def submit(request: Request) -> dict[str, str]:
        access.protect(request)
        payload = await _read_object(request)
        if set(payload) != {"snapshot_id", "config"}:
            raise ValueError("研究需要 snapshot_id 和 config，不能提供账户状态。")
        snapshot_text = payload["snapshot_id"]
        if not isinstance(snapshot_text, str):
            raise ValueError("snapshot_id 必须是规范的 UUID。")
        snapshot_id = UUID(snapshot_text)
        if str(snapshot_id) != snapshot_text:
            raise ValueError("snapshot_id 必须是规范的 UUID。")
        configuration = ResearchConfig.from_mapping(_object(payload["config"]))

        def execute() -> dict[str, str]:
            dataset = library.load_dataset(snapshot_id)
            result = run_research(dataset, configuration)
            run_id = store.save(dataset, configuration, result)
            return {"run_id": run_id, "url": f"/runs/{run_id}"}

        return await run_in_threadpool(execute)
