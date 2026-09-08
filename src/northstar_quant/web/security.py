from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint

from northstar_quant.data_management.library import AdmissionRejected
from northstar_quant.live import CommandUnknown, RuntimeUnavailable
from northstar_quant.web_access import (
    LocalWorkspaceMiddleware,
    WorkspaceAccess,
    content_security_policy,
)

_UI_PAGE = re.compile(r"/(?:streams/[0-9a-f-]{36})?\Z")


def install(app: FastAPI, access: WorkspaceAccess) -> None:
    app.add_middleware(LocalWorkspaceMiddleware, access=access)

    @app.exception_handler(RuntimeUnavailable)
    async def live_unavailable(_request: Request, _error: RuntimeUnavailable) -> JSONResponse:
        return JSONResponse(
            {"detail": "Live 不可用：不能确认当前运行状态。已有观察不是在线证明。"},
            status_code=503,
        )

    @app.exception_handler(CommandUnknown)
    async def command_unknown(_request: Request, error: CommandUnknown) -> JSONResponse:
        identifier = str(error.request_id)
        return JSONResponse(
            {
                "detail": "命令结果未知。请按此固定身份查询，勿新建命令或盲目重发。",
                "request_id": identifier,
                "runtime_id": None if error.runtime_id is None else str(error.runtime_id),
                "status": "UNKNOWN",
                "url": f"/live/commands/{identifier}",
            },
            status_code=503,
        )

    @app.middleware("http")
    async def local_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
        ui_session = None
        if request.method == "GET" and _UI_PAGE.fullmatch(request.url.path):
            ui_session = access.open(request)
        response = await call_next(request)
        ui_html = None
        if ui_session is not None and response.headers.get("content-type", "").startswith(
            "text/html"
        ):
            if not hasattr(response, "body_iterator"):
                raise RuntimeError("HTTP middleware did not return a streamed page")
            ui_html = b"".join([chunk async for chunk in response.body_iterator])
            response = Response(
                ui_html,
                status_code=response.status_code,
                headers=dict(response.headers),
                background=response.background,
            )
        if ui_session is not None:
            access.set_cookie(request, response, ui_session)
        response.headers["Content-Security-Policy"] = content_security_policy(ui_html)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, _error: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            {"detail": "数据库暂时不可用。请确认数据库已启动并完成初始化后重试。"},
            status_code=503,
        )

    @app.exception_handler(ValueError)
    async def input_error(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(error)[:500]}, status_code=422)

    @app.exception_handler(AdmissionRejected)
    async def rejected_source(_request: Request, error: AdmissionRejected) -> JSONResponse:
        return JSONResponse(
            {"detail": str(error)[:500], "rejection_id": error.rejection_id}, status_code=422
        )

    @app.exception_handler(PermissionError)
    async def forbidden_asset(_request: Request, _error: PermissionError) -> JSONResponse:
        return JSONResponse({"detail": "当前身份没有此操作权限。"}, status_code=403)

    @app.exception_handler(LookupError)
    async def missing_resource(_request: Request, _error: LookupError) -> JSONResponse:
        return JSONResponse({"detail": "没有找到这份数据、配置或运行。"}, status_code=404)
