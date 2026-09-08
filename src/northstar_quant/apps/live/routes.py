from __future__ import annotations

import json
from typing import cast
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from northstar_quant.live import LiveClient
from northstar_quant.web.html import _text, workspace_page
from northstar_quant.web.requests import _read_object, _string_field, _uuid_field
from northstar_quant.web_access import (
    WorkspaceAccess,
)

from . import broker_views
from .commands import _runtime_header


def register(app: FastAPI, access: WorkspaceAccess, live: LiveClient) -> None:
    broker = live.broker

    @app.get("/api/live/status")
    async def live_status(request: Request) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(live.status)

    @app.get("/api/live/commands/{request_id}")
    async def live_command(request: Request, request_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(live.command, request_id)

    @app.get("/live/commands/{request_id}", response_class=HTMLResponse)
    async def command_detail(request: Request, request_id: UUID) -> HTMLResponse:
        runtime = await run_in_threadpool(live.status)
        result = await run_in_threadpool(live.command, request_id)
        content = (
            "<section class='panel'><h1>固定命令结果查询</h1>"
            "<p>只读查询，不重新提交、不替换目标运行、不恢复执行权。"
            "未完成或未知必须保留原身份核查。</p><pre>"
            + _text(json.dumps(result, ensure_ascii=False, indent=2))
            + "</pre></section>"
        )
        return workspace_page(
            access,
            request,
            "Live 命令结果",
            content,
            mode="Live · 只读核查",
            runtime_id=UUID(str(runtime["runtime_id"])),
        )

    @app.get("/broker", response_class=HTMLResponse)
    async def broker_home(request: Request) -> HTMLResponse:
        runtime = await run_in_threadpool(live.status)

        def content() -> str:
            return broker_views.workspace(broker.status(), broker.list())

        return workspace_page(
            access,
            request,
            "SimNow 连接",
            await run_in_threadpool(content),
            mode="SimNow · 只读验收",
            runtime_id=UUID(str(runtime["runtime_id"])),
        )

    @app.get("/broker/{batch_id}", response_class=HTMLResponse)
    async def broker_detail(request: Request, batch_id: UUID) -> HTMLResponse:
        runtime = await run_in_threadpool(live.status)

        def content() -> str:
            return broker_views.report(
                broker.get(batch_id),
                broker.baseline_context(batch_id),
                broker.ledger_context(batch_id),
                broker.funds_context(batch_id),
            )

        return workspace_page(
            access,
            request,
            "SimNow 查询记录",
            await run_in_threadpool(content),
            mode="SimNow · 固定查询记录",
            runtime_id=UUID(str(runtime["runtime_id"])),
        )

    @app.get("/api/broker/status")
    async def broker_status(request: Request) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.status)

    @app.get("/api/broker/queries")
    async def broker_queries(request: Request, limit: int = 50) -> list[dict[str, object]]:
        access.require_request(request)
        return await run_in_threadpool(broker.list, limit=limit)

    @app.get("/api/broker/queries/{batch_id}")
    async def broker_query_detail(request: Request, batch_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get, batch_id)

    @app.get("/api/broker/queries/{batch_id}/baseline-context")
    async def broker_baseline_context(request: Request, batch_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.baseline_context, batch_id)

    @app.post("/api/broker/baselines")
    async def broker_establish_baseline(request: Request) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"source_batch_id", "request_id"}:
            raise ValueError("建立基准只接受 source_batch_id 和 request_id，不接受资金或仓位。")
        return await run_in_threadpool(
            command_live.broker.establish_baseline,
            _uuid_field(payload, "source_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.post("/api/broker/baseline-checks")
    async def broker_compare_baseline(request: Request) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"baseline_id", "query_batch_id", "request_id"}:
            raise ValueError("比较只接受 baseline_id、query_batch_id 和 request_id。")
        return await run_in_threadpool(
            command_live.broker.compare_baseline,
            _uuid_field(payload, "baseline_id"),
            _uuid_field(payload, "query_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get("/api/broker/baseline-checks/{check_id}")
    async def broker_baseline_check(request: Request, check_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_baseline_check, check_id)

    @app.get("/api/broker/queries/{batch_id}/ledger-context")
    async def broker_ledger_context(request: Request, batch_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.ledger_context, batch_id)

    @app.post("/api/broker/position-entries")
    async def broker_ingest_positions(request: Request) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"baseline_id", "source_batch_id", "request_id"}:
            raise ValueError("入账只接受 baseline_id、source_batch_id 和 request_id。")
        return await run_in_threadpool(
            command_live.broker.ingest_positions,
            _uuid_field(payload, "baseline_id"),
            _uuid_field(payload, "source_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get("/api/broker/position-entries/{entry_id}")
    async def broker_position_entry(request: Request, entry_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_position_entry, entry_id)

    @app.post("/api/broker/funds-entries")
    async def broker_observe_funds(request: Request) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"baseline_id", "source_batch_id", "request_id"}:
            raise ValueError("资金登记只接受已存基准、查询与命令身份，不接受手工金额。")
        return await run_in_threadpool(
            command_live.broker.observe_funds,
            _uuid_field(payload, "baseline_id"),
            _uuid_field(payload, "source_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get("/api/broker/funds-entries/{entry_id}")
    async def broker_funds_entry(request: Request, entry_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_funds_entry, entry_id)

    @app.get("/broker/funds/{entry_id}", response_class=HTMLResponse)
    async def broker_funds_detail(request: Request, entry_id: UUID) -> HTMLResponse:
        runtime = await run_in_threadpool(live.status)
        result = await run_in_threadpool(broker.get_funds_entry, entry_id)
        return workspace_page(
            access,
            request,
            "账户资金与累计费用",
            broker_views.funds_report(result),
            mode="SimNow · 固定账户事实",
            runtime_id=UUID(str(runtime["runtime_id"])),
        )

    @app.post("/api/streams/{stream_id}/account-catchup")
    async def broker_stream_account_catchup(request: Request, stream_id: UUID) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if (
            set(payload) != {"baseline_id", "through_sequence", "request_id"}
            or type(payload.get("through_sequence")) is not int
        ):
            raise ValueError("账户补处理只接受固定基准和整数来源上界，不接受资金、仓位或执行权限。")
        return await run_in_threadpool(
            command_live.streams.catchup_account,
            stream_id,
            _uuid_field(payload, "baseline_id"),
            cast(int, payload["through_sequence"]),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.post("/api/streams/{stream_id}/position-entries")
    async def broker_stream_positions(request: Request, stream_id: UUID) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if (
            set(payload) != {"baseline_id", "through_sequence", "request_id"}
            or type(payload.get("through_sequence")) is not int
        ):
            raise ValueError("流成交入账只接受基准、整数前缀序号和命令身份。")
        return await run_in_threadpool(
            command_live.broker.ingest_stream_positions,
            _uuid_field(payload, "baseline_id"),
            stream_id,
            cast(int, payload["through_sequence"]),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.post("/api/broker/position-checks")
    async def broker_compare_positions(request: Request) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"entry_id", "query_batch_id", "request_id"}:
            raise ValueError("持仓比较只接受 entry_id、query_batch_id 和 request_id。")
        return await run_in_threadpool(
            command_live.broker.compare_positions,
            _uuid_field(payload, "entry_id"),
            _uuid_field(payload, "query_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get("/api/broker/position-checks/{check_id}")
    async def broker_position_check(request: Request, check_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_position_check, check_id)

    @app.post("/api/broker/order-checks")
    async def broker_check_orders(request: Request) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"position_check_id", "request_id"}:
            raise ValueError("委托核对只接受 position_check_id 和 request_id，不接受手工事实。")
        return await run_in_threadpool(
            command_live.broker.check_orders,
            _uuid_field(payload, "position_check_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get("/api/broker/order-checks/{check_id}")
    async def broker_order_check(request: Request, check_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_order_check, check_id)

    @app.post("/api/broker/queries")
    async def broker_query(request: Request) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(_runtime_header(request))
        payload = await _read_object(request)
        if set(payload) != {"profile", "instrument", "request_id"}:
            raise ValueError("查询只接受 profile、instrument 和 request_id；网页不接收凭据或地址。")
        return await run_in_threadpool(
            command_live.broker.query,
            _string_field(payload, "profile"),
            _string_field(payload, "instrument"),
            request_id=_uuid_field(payload, "request_id"),
        )
