from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from northstar_quant.broker import budget_views, stream_views
from northstar_quant.broker import views as broker_views
from northstar_quant.live import LiveClient
from northstar_quant.sessions import SessionStore
from northstar_quant.web.html import _text, workspace_page
from northstar_quant.web.requests import _read_object, _string_field, _uuid_field
from northstar_quant.web_access import (
    WorkspaceAccess,
)


def register(app: FastAPI, access: WorkspaceAccess, live: LiveClient, paper: SessionStore) -> None:
    broker, streams, opening_budgets = live.broker, live.streams, live.opening_budgets

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

    @app.get("/streams", response_class=HTMLResponse)
    async def stream_home(request: Request) -> HTMLResponse:
        runtime = await run_in_threadpool(live.status)

        def content() -> str:
            return stream_views.workspace(
                broker.list(), paper.list_configurations(), streams.list()
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


def _runtime_header(request: Request) -> UUID:
    value = request.headers.get("x-live-runtime-id", "")
    try:
        identifier = UUID(value)
    except ValueError as error:
        raise ValueError("Live 操作必须绑定本页已观察的运行身份；请重新打开页面。") from error
    if str(identifier) != value:
        raise ValueError("Live 运行身份必须是规范 UUID。")
    return identifier
