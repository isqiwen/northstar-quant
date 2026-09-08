"""Read-only fixed dataset presentation shared by Data Hub and Research."""

from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.web.data_views import _dataset_page, _lineage_panel
from northstar_quant.web.html import workspace_page
from northstar_quant.web_access import WorkspaceAccess


def register(app: FastAPI, access: WorkspaceAccess, library: DataLibrary) -> None:
    @app.get("/api/datasets")
    def accepted_datasets(limit: int = 50) -> list[dict[str, object]]:
        return [item.to_dict() for item in library.list_datasets(limit=limit)]

    @app.get("/api/datasets/{snapshot_id}")
    def dataset_details(snapshot_id: UUID) -> dict[str, object]:
        return library.describe_dataset(snapshot_id).to_dict()

    @app.get("/api/datasets/{snapshot_id}/lineage")
    def dataset_lineage(snapshot_id: UUID) -> dict[str, object]:
        return library.lineage(snapshot_id)

    @app.get("/datasets/{snapshot_id}", response_class=HTMLResponse)
    async def show_dataset(request: Request, snapshot_id: UUID) -> HTMLResponse:
        data = await run_in_threadpool(library.describe_dataset, snapshot_id)
        lineage = await run_in_threadpool(library.lineage, snapshot_id)
        return workspace_page(
            access, request, "数据详情", _dataset_page(data.to_dict()) + _lineage_panel(lineage)
        )
