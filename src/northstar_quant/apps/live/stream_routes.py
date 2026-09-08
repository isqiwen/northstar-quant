from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from northstar_quant.live import LiveClient
from northstar_quant.web.html import workspace_page
from northstar_quant.web.requests import _read_object, _string_field, _uuid_field
from northstar_quant.web_access import (
    WorkspaceAccess,
)

from . import budget_views, stream_views
from .commands import _runtime_header


def register(app: FastAPI, access: WorkspaceAccess, live: LiveClient) -> None:
    broker, streams, opening_budgets = live.broker, live.streams, live.opening_budgets

    @app.get("/streams", response_class=HTMLResponse)
    async def stream_home(request: Request) -> HTMLResponse:
        runtime = await run_in_threadpool(live.status)

        def content() -> str:
            return stream_views.workspace(
                broker.list(), live.read_list("/configurations"), streams.list()
            )

        return workspace_page(
            access,
            request,
            "持续行情与影子策略",
            await run_in_threadpool(content),
            mode="SimNow · 不发单",
            runtime_id=UUID(str(runtime["runtime_id"])),
        )

    @app.post("/api/streams/{stream_id}/opening-budgets")
    async def opening_budget_create(request: Request, stream_id: UUID) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"sequence", "order_check_id", "limit_price", "request_id"}:
            raise ValueError("开仓预算只接受已保存步骤、委托核对、限价和命令身份。")
        if type(payload["sequence"]) is not int:
            raise ValueError("sequence 必须是整数。")
        try:
            limit_price = Decimal(_string_field(payload, "limit_price"))
        except InvalidOperation as error:
            raise ValueError("限价必须是十进制数字字符串。") from error
        return await run_in_threadpool(
            command_live.opening_budgets.create,
            stream_id,
            payload["sequence"],
            _uuid_field(payload, "order_check_id"),
            limit_price=limit_price,
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get("/api/broker/opening-budgets/{budget_id}")
    async def opening_budget_get(request: Request, budget_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(opening_budgets.get, budget_id)

    @app.get("/broker/opening-budgets/{budget_id}", response_class=HTMLResponse)
    async def opening_budget_page(request: Request, budget_id: UUID) -> HTMLResponse:
        runtime = await run_in_threadpool(live.status)
        result = await run_in_threadpool(opening_budgets.get, budget_id)
        return workspace_page(
            access,
            request,
            "固定开仓预算",
            budget_views.report(result),
            mode="历史预算 · 不发单",
            runtime_id=UUID(str(runtime["runtime_id"])),
        )

    @app.post("/api/streams", status_code=201)
    async def stream_start(request: Request) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {
            "query_batch_id",
            "configuration_id",
            "request_id",
            "duration_seconds",
            "allow_retention",
            "use_basis",
        }:
            raise ValueError("持续接收只接受查询、固定配置、时长及留存声明；不接受凭据或地址。")
        return await run_in_threadpool(
            command_live.streams.start,
            _uuid_field(payload, "query_batch_id"),
            _string_field(payload, "configuration_id"),
            request_id=_uuid_field(payload, "request_id"),
            duration_seconds=cast(int, payload["duration_seconds"]),
            allow_retention=cast(bool, payload["allow_retention"]),
            use_basis=_string_field(payload, "use_basis"),
        )

    @app.get("/api/streams/{stream_id}")
    async def stream_state(request: Request, stream_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(streams.get, stream_id)

    @app.get("/api/streams/{stream_id}/events")
    async def stream_events(
        request: Request, stream_id: UUID, after: int = 0
    ) -> list[dict[str, object]]:
        access.require_request(request)
        return await run_in_threadpool(streams.events, stream_id, after=after)

    @app.post("/api/streams/{stream_id}/control")
    async def stream_control(request: Request, stream_id: UUID) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"action", "request_id"}:
            raise ValueError("会话控制只接受 action 和 request_id。")
        return await run_in_threadpool(
            command_live.streams.control,
            stream_id,
            _string_field(payload, "action"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.post("/api/streams/{stream_id}/archive")
    async def stream_archive(request: Request, stream_id: UUID) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {
            "through_sequence",
            "session_open",
            "session_close",
            "allow_download",
            "request_id",
        }:
            raise ValueError("归档只接受固定前缀、UTC 范围、下载许可和命令身份。")
        if (
            type(payload["through_sequence"]) is not int
            or type(payload["allow_download"]) is not bool
        ):
            raise ValueError("through_sequence 必须是整数，allow_download 必须是布尔值。")
        return await run_in_threadpool(
            command_live.streams.archive,
            stream_id,
            through_sequence=payload["through_sequence"],
            session_open=_string_field(payload, "session_open"),
            session_close=_string_field(payload, "session_close"),
            allow_download=payload["allow_download"],
            request_id=_uuid_field(payload, "request_id"),
        )
