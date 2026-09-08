from __future__ import annotations

import json
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint

from northstar_quant.data_management.library import AdmissionRejected
from northstar_quant.live import CommandUnknown, RuntimeUnavailable
from northstar_quant.web import common_pb2
from northstar_quant.web.access import (
    LocalWorkspaceMiddleware,
    WorkspaceAccess,
    content_security_policy,
)
from northstar_quant.web.protobuf import MEDIA_TYPE, pack


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
        response = await call_next(request)
        route = request.scope.get("route")
        method = getattr(app.state, "protobuf_methods", {}).get(
            (request.method, getattr(route, "path", ""))
        )
        if method is not None and response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            content = b"".join([chunk async for chunk in cast(Any, response).body_iterator])
            value = json.loads(content)
            descriptor = (
                method.output_type if response.status_code < 400 else common_pb2.Error.DESCRIPTOR
            )
            encoded = pack(descriptor, value).SerializeToString()
            headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in {"content-length", "content-type"}
            }
            response = Response(
                encoded, status_code=response.status_code, media_type=MEDIA_TYPE, headers=headers
            )
        response.headers["Content-Security-Policy"] = content_security_policy()
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

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(part) for part in item["loc"]) for item in error.errors()]
        return JSONResponse(
            {"detail": "请求字段或类型不符合接口定义：" + ", ".join(fields)[:400]}, status_code=422
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
