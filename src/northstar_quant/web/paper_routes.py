from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from northstar_quant.data.library import DataLibrary
from northstar_quant.research import ResearchConfig
from northstar_quant.sessions import SessionStore
from northstar_quant.web.html import workspace_page
from northstar_quant.web.paper_views import _paper_report, _paper_workspace
from northstar_quant.web.requests import _object, _read_object, _uuid_field
from northstar_quant.web_access import (
    WorkspaceAccess,
)


def register(
    app: FastAPI, access: WorkspaceAccess, library: DataLibrary, paper: SessionStore
) -> None:
    @app.get("/paper", response_class=HTMLResponse)
    async def paper_home(request: Request) -> HTMLResponse:
        def workspace() -> str:
            datasets = [item.to_dict() for item in library.list_datasets()]
            return _paper_workspace(paper.list_configurations(), datasets, paper.list())

        return workspace_page(
            access,
            request,
            "文件 Paper 工作台",
            await run_in_threadpool(workspace),
            mode="内部 Paper · 文件输入",
        )

    @app.get("/paper/{session_id}", response_class=HTMLResponse)
    async def paper_detail(request: Request, session_id: UUID) -> HTMLResponse:
        result = await run_in_threadpool(paper.get, session_id)
        return workspace_page(
            access, request, "文件 Paper 会话", _paper_report(result), mode="内部 Paper · 文件输入"
        )

    @app.get("/api/configurations")
    def list_configurations() -> list[dict[str, object]]:
        return paper.list_configurations()

    @app.post("/api/configurations", status_code=201)
    async def save_configuration(request: Request) -> dict[str, object]:
        access.protect(request)
        payload = await _read_object(request)
        if set(payload) != {"name", "config"} or not isinstance(payload["name"], str):
            raise ValueError("保存配置需要 name 和 config，不能提供账户状态或执行权限。")
        configuration = ResearchConfig.from_mapping(_object(payload["config"]))
        return await run_in_threadpool(paper.save_configuration, payload["name"], configuration)

    @app.get("/api/paper")
    def list_paper() -> list[dict[str, object]]:
        return paper.list()

    @app.post("/api/paper", status_code=201)
    async def create_paper(request: Request) -> dict[str, object]:
        access.protect(request)
        payload = await _read_object(request)
        if set(payload) != {"snapshot_id", "configuration_id", "request_id"}:
            raise ValueError("新建 Paper 只接受 snapshot_id、configuration_id 和 request_id。")
        configuration_id = payload["configuration_id"]
        if not isinstance(configuration_id, str):
            raise ValueError("configuration_id 必须是已保存的配置身份。")
        result = await run_in_threadpool(
            paper.create,
            _uuid_field(payload, "snapshot_id"),
            configuration_id,
            request_id=_uuid_field(payload, "request_id"),
        )
        return result

    @app.get("/api/paper/{session_id}")
    def get_paper(session_id: UUID) -> dict[str, object]:
        return paper.get(session_id)

    @app.post("/api/paper/{session_id}/advance")
    async def advance_paper(request: Request, session_id: UUID) -> dict[str, object]:
        access.protect(request)
        payload = await _read_object(request)
        if set(payload) != {"request_id"}:
            raise ValueError("推进只接受 request_id；不能注入游标、账户状态或新配置。")
        return await run_in_threadpool(
            paper.advance, session_id, request_id=_uuid_field(payload, "request_id")
        )
