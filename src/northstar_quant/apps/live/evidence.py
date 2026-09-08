"""Show Live-owned archive evidence without opening storage in the management process."""

import base64
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from northstar_quant.live import LiveClient
from northstar_quant.web.data_views import _attempt_page, _dataset_page, _source_page
from northstar_quant.web.html import workspace_page
from northstar_quant.web.requests import _object, _read_object, _uuid_field
from northstar_quant.web_access import WorkspaceAccess

from .commands import _runtime_header


def register(app: FastAPI, access: WorkspaceAccess, live: LiveClient) -> None:
    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    async def source(request: Request, source_id: UUID) -> HTMLResponse:
        result = await run_in_threadpool(live.read, f"/sources/{source_id}")
        return workspace_page(access, request, "Live 来源", _source_page(result))

    @app.get("/attempts/{attempt_id}", response_class=HTMLResponse)
    async def attempt(request: Request, attempt_id: UUID) -> HTMLResponse:
        result = await run_in_threadpool(live.read, f"/attempts/{attempt_id}")
        return workspace_page(access, request, "Live 加工", _attempt_page(result))

    @app.get("/datasets/{snapshot_id}", response_class=HTMLResponse)
    async def dataset(request: Request, snapshot_id: UUID) -> HTMLResponse:
        result = await run_in_threadpool(live.read, f"/datasets/{snapshot_id}")
        return workspace_page(access, request, "Live 固定数据", _dataset_page(result))

    @app.post("/api/sources/{source_id}/reprocess")
    async def reprocess(request: Request, source_id: UUID) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"spec", "request_id"}:
            raise ValueError("重处理只接受固定参数与命令身份。")
        return await run_in_threadpool(
            command_live.mutate,
            f"/sources/{source_id}/reprocess",
            {"spec": _object(payload["spec"])},
            _uuid_field(payload, "request_id"),
        )

    @app.get("/api/sources/{source_id}/download")
    async def download(request: Request, source_id: UUID) -> Response:
        access.require_request(request)
        payload = await run_in_threadpool(live.read, f"/sources/{source_id}/download")
        return Response(
            base64.b64decode(payload["content_base64"], validate=True),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(payload["filename"], safe="")
            },
        )
