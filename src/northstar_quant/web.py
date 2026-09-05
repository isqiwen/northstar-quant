"""A local, same-origin workspace for importing data and inspecting research."""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import time
from decimal import Decimal
from html import escape
from importlib.resources import files
from typing import cast
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint

from northstar_quant.broker import views as broker_views
from northstar_quant.broker.workspace import BrokerWorkspace
from northstar_quant.data.files import SourceFiles
from northstar_quant.data.library import AdmissionRejected, DataLibrary
from northstar_quant.research import ResearchConfig, run_research
from northstar_quant.runs import RunStore
from northstar_quant.sessions import SessionStore

_MAX_BODY = 8 * 1024 * 1024
_LOCAL_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::[1-9][0-9]{0,4})?\Z")
_CSV_COLUMNS = "event_time,available_at,source_record_id,open,high,low,close,volume"
_WORKSPACE_COOKIE = "northstar_workspace_session"
_WORKSPACE_SESSION_SECONDS = 1800
_INPUT_KIND_LABELS = {
    "RECEIVED_CSV": "实际收到的 CSV（不宣称供应商原文）",
    "CONVERTED_CSV": "外部转换后 CSV",
}
_PROCESS_LABELS = {
    "PENDING": "等待处理",
    "RUNNING": "正在处理",
    "FAILED": "失败",
    "PUBLISHED": "已发布",
    "RECEIVED": "已接收",
    "VALIDATING": "检查处理参数",
    "PARSING": "解析文件",
    "IMPORTING": "写入观测",
    "QUALITY": "质量检查",
    "PUBLISHING": "发布快照",
}
_DECISION_LABELS = {
    "BUY": "买入",
    "SELL": "卖出",
    "ALLOW": "通过",
    "REDUCE": "缩减",
    "REJECT": "拒绝",
    "UNKNOWN": "状态不足",
    "CONTRACT_MISMATCH": "合约不匹配",
    "ACCOUNT_NOT_CURRENT": "账户状态未更新至决策时刻",
    "INTENT_EXPIRED": "意图已失效",
    "NONPOSITIVE_EQUITY": "权益不足",
    "NO_PERMITTED_POSITION": "限制不允许持仓",
    "TARGET_UNCHANGED": "目标持仓未变化",
    "NO_SAFE_FILL_PRICE": "无满足限制的成交价格",
    "REVERSAL_REDUCED_TO_FLAT": "反向目标先平仓",
    "RISK_REDUCING_TARGET": "降低已有敞口",
    "CAPPED_TO_LIMIT": "已按限额缩减目标",
    "WITHIN_LIMITS": "目标在限额内",
}


def application() -> FastAPI:
    """Create the installed application for the supported ASGI command."""

    from northstar_quant.db import open_database

    engine = open_database()
    return create_app(engine, DataLibrary(engine, SourceFiles.from_environment()))


def create_app(engine: Engine, library: DataLibrary) -> FastAPI:
    store = RunStore(engine)
    paper = SessionStore(engine, library)
    broker = BrokerWorkspace(engine)
    app = FastAPI(title="Northstar · 个人量化工作台", docs_url=None, redoc_url=None)
    browser_sessions: dict[str, tuple[str, float]] = {}

    def workspace_page(
        request: Request, title: str, content: str, *, mode: str = "历史研究 · 本机"
    ) -> HTMLResponse:
        # Called on the event loop, as is command protection below. Tokens are
        # process-local browser sessions, not broker execution authority.
        now = time.monotonic()
        for identifier, (_, deadline) in list(browser_sessions.items()):
            if deadline <= now:
                del browser_sessions[identifier]
        identifier = request.cookies.get(_WORKSPACE_COOKIE, "")
        if identifier not in browser_sessions:
            if len(browser_sessions) >= 64:
                del browser_sessions[next(iter(browser_sessions))]
            identifier = secrets.token_urlsafe(32)
            browser_sessions[identifier] = (
                secrets.token_urlsafe(32),
                now + _WORKSPACE_SESSION_SECONDS,
            )
        csrf, deadline = browser_sessions[identifier]
        response = HTMLResponse(_page(title, content, csrf=csrf, mode=mode))
        response.set_cookie(
            _WORKSPACE_COOKIE,
            identifier,
            max_age=max(1, int(deadline - now)),
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return response

    def require_workspace_session(request: Request) -> str:
        session = browser_sessions.get(request.cookies.get(_WORKSPACE_COOKIE, ""))
        if session is None or session[1] <= time.monotonic():
            raise HTTPException(
                status_code=403,
                detail="工作台会话缺失或已过期。请重新打开工作台页面后操作。",
            )
        return session[0]

    def protect_workspace_command(request: Request) -> None:
        expected = require_workspace_session(request)
        supplied = request.headers.get("x-northstar-csrf", "")
        if not supplied.isascii() or not secrets.compare_digest(expected, supplied):
            raise HTTPException(
                status_code=403,
                detail="工作台操作校验失败。请重新打开页面后操作。",
            )

    @app.middleware("http")
    async def local_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
        authority = request.headers.get("host", "")
        if _LOCAL_HOST.fullmatch(authority) is None:
            return JSONResponse({"detail": "仅接受本机访问。"}, status_code=403)
        if ":" in authority and int(authority.rsplit(":", 1)[1]) > 65535:
            return JSONResponse({"detail": "无效的本机地址。"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            expected_origin = f"{request.url.scheme}://{authority}"
            if origin is not None and origin != expected_origin:
                return JSONResponse({"detail": "仅接受同源操作。"}, status_code=403)
            if request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
                return JSONResponse({"detail": "仅接受同源操作。"}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
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
        return JSONResponse({"detail": "此来源未获准下载。"}, status_code=403)

    @app.exception_handler(LookupError)
    async def missing_resource(_request: Request, _error: LookupError) -> JSONResponse:
        return JSONResponse({"detail": "没有找到这份数据、配置或运行。"}, status_code=404)

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        from northstar_quant.db import require_current_database

        try:
            require_current_database(engine)
        except ValueError as error:
            raise HTTPException(status_code=503, detail="数据库尚未初始化为当前版本。") from error
        store.list(limit=1)
        return {"status": "ready"}

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

        return workspace_page(request, "研究工作台", await run_in_threadpool(workspace))

    @app.get("/assets/app.css")
    def stylesheet() -> Response:
        return Response(
            files("northstar_quant").joinpath("static", "app.css").read_text("utf-8"),
            media_type="text/css",
        )

    @app.get("/assets/app.js")
    def javascript() -> Response:
        return Response(
            files("northstar_quant").joinpath("static", "app.js").read_text("utf-8"),
            media_type="application/javascript",
        )

    @app.get("/api/runs")
    def list_runs(limit: int = 50) -> list[dict[str, object]]:
        return store.list(limit=limit)

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
            request, "数据详情", _dataset_page(data.to_dict()) + _lineage_panel(lineage)
        )

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        return store.get(run_id)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def show_run(request: Request, run_id: str) -> HTMLResponse:
        run = await run_in_threadpool(store.get, run_id)
        snapshot_id = UUID(str(_object(run["snapshot"])["id"]))
        lineage = await run_in_threadpool(library.lineage, snapshot_id)
        return workspace_page(request, "研究结果", _report(run) + _lineage_panel(lineage))

    @app.get("/broker", response_class=HTMLResponse)
    async def broker_home(request: Request) -> HTMLResponse:
        def content() -> str:
            return broker_views.workspace(broker.status(), broker.list())

        return workspace_page(
            request, "SimNow 连接", await run_in_threadpool(content), mode="SimNow · 只读验收"
        )

    @app.get("/broker/{batch_id}", response_class=HTMLResponse)
    async def broker_detail(request: Request, batch_id: UUID) -> HTMLResponse:
        batch = await run_in_threadpool(broker.get, batch_id)
        return workspace_page(
            request, "SimNow 查询记录", broker_views.report(batch), mode="SimNow · 固定查询记录"
        )

    @app.get("/api/broker/status")
    async def broker_status(request: Request) -> dict[str, object]:
        require_workspace_session(request)
        return await run_in_threadpool(broker.status)

    @app.get("/api/broker/queries")
    async def broker_queries(request: Request, limit: int = 50) -> list[dict[str, object]]:
        require_workspace_session(request)
        return await run_in_threadpool(broker.list, limit=limit)

    @app.get("/api/broker/queries/{batch_id}")
    async def broker_query_detail(request: Request, batch_id: UUID) -> dict[str, object]:
        require_workspace_session(request)
        return await run_in_threadpool(broker.get, batch_id)

    @app.post("/api/broker/queries")
    async def broker_query(request: Request) -> dict[str, object]:
        protect_workspace_command(request)
        payload = await _read_object(request)
        if set(payload) != {"profile", "instrument", "request_id"}:
            raise ValueError("查询只接受 profile、instrument 和 request_id；网页不接收凭据或地址。")
        return await run_in_threadpool(
            broker.query,
            _string_field(payload, "profile"),
            _string_field(payload, "instrument"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get("/paper", response_class=HTMLResponse)
    async def paper_home(request: Request) -> HTMLResponse:
        def workspace() -> str:
            datasets = [item.to_dict() for item in library.list_datasets()]
            return _paper_workspace(paper.list_configurations(), datasets, paper.list())

        return workspace_page(
            request,
            "文件 Paper 工作台",
            await run_in_threadpool(workspace),
            mode="内部 Paper · 文件输入",
        )

    @app.get("/paper/{session_id}", response_class=HTMLResponse)
    async def paper_detail(request: Request, session_id: UUID) -> HTMLResponse:
        result = await run_in_threadpool(paper.get, session_id)
        return workspace_page(
            request, "文件 Paper 会话", _paper_report(result), mode="内部 Paper · 文件输入"
        )

    @app.get("/api/configurations")
    def list_configurations() -> list[dict[str, object]]:
        return paper.list_configurations()

    @app.post("/api/configurations", status_code=201)
    async def save_configuration(request: Request) -> dict[str, object]:
        protect_workspace_command(request)
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
        protect_workspace_command(request)
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
        protect_workspace_command(request)
        payload = await _read_object(request)
        if set(payload) != {"request_id"}:
            raise ValueError("推进只接受 request_id；不能注入游标、账户状态或新配置。")
        return await run_in_threadpool(
            paper.advance, session_id, request_id=_uuid_field(payload, "request_id")
        )

    @app.post("/api/import")
    async def upload(request: Request) -> dict[str, object]:
        protect_workspace_command(request)
        payload = await _read_object(request)
        if set(payload) != {
            "content_base64",
            "filename",
            "source_name",
            "use_basis",
            "allow_retention",
            "allow_download",
            "input_kind",
            "upstream_source_id",
            "transformation_note",
            "spec",
            "request_id",
        }:
            raise ValueError(
                "上传需要原文字节、来源与权限声明、处理参数和命令身份，不能提供本地路径。"
            )
        encoded = _string_field(payload, "content_base64")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("content_base64 必须是严格 Base64 编码的原始文件字节。") from error
        for name in ("allow_retention", "allow_download"):
            if type(payload[name]) is not bool:
                raise ValueError(f"{name} 必须是明确的布尔值。")
        upstream = payload["upstream_source_id"]
        note = payload["transformation_note"]
        if note is not None and not isinstance(note, str):
            raise ValueError("transformation_note 必须是文本或 null。")
        return await run_in_threadpool(
            library.receive,
            content,
            filename=_string_field(payload, "filename"),
            source_name=_string_field(payload, "source_name"),
            use_basis=_string_field(payload, "use_basis"),
            allow_retention=cast(bool, payload["allow_retention"]),
            allow_download=cast(bool, payload["allow_download"]),
            input_kind=_string_field(payload, "input_kind"),
            upstream_source_id=None
            if upstream is None
            else _uuid_field(payload, "upstream_source_id"),
            transformation_note=note,
            spec=_object(payload["spec"]),
            request_id=str(_uuid_field(payload, "request_id")),
        )

    @app.get("/sources", response_class=HTMLResponse)
    async def sources_home(request: Request) -> HTMLResponse:
        def content() -> str:
            return _sources_workspace(
                library.list_sources(), library.list_attempts(), library.list_rejections()
            )

        return workspace_page(
            request, "来源与处理", await run_in_threadpool(content), mode="本机来源归档"
        )

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    async def source_page(request: Request, source_id: UUID) -> HTMLResponse:
        source = await run_in_threadpool(library.source, source_id)
        return workspace_page(request, "来源详情", _source_page(source), mode="本机来源归档")

    @app.get("/attempts/{attempt_id}", response_class=HTMLResponse)
    async def attempt_page(request: Request, attempt_id: UUID) -> HTMLResponse:
        attempt = await run_in_threadpool(library.attempt, attempt_id)
        return workspace_page(request, "处理尝试", _attempt_page(attempt), mode="本机来源处理")

    @app.get("/api/sources")
    def list_sources(limit: int = 50) -> list[dict[str, object]]:
        return library.list_sources(limit=limit)

    @app.get("/api/sources/{source_id}")
    def source_details(source_id: UUID) -> dict[str, object]:
        return library.source(source_id)

    @app.get("/api/attempts")
    def list_attempts(limit: int = 50) -> list[dict[str, object]]:
        return library.list_attempts(limit=limit)

    @app.get("/api/attempts/{attempt_id}")
    def attempt_details(attempt_id: UUID) -> dict[str, object]:
        return library.attempt(attempt_id)

    @app.get("/api/sources/{source_id}/download")
    async def download_source(request: Request, source_id: UUID) -> Response:
        require_workspace_session(request)
        filename, content = await run_in_threadpool(library.download, source_id)
        return Response(
            content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
            },
        )

    @app.post("/api/sources/{source_id}/reprocess")
    async def reprocess_source(request: Request, source_id: UUID) -> dict[str, object]:
        protect_workspace_command(request)
        payload = await _read_object(request)
        if set(payload) != {"spec", "request_id"}:
            raise ValueError("重处理只接受 spec 和 request_id，不能更换原文或来源权限。")
        return await run_in_threadpool(
            library.reprocess,
            source_id,
            spec=_object(payload["spec"]),
            request_id=str(_uuid_field(payload, "request_id")),
        )

    @app.post("/api/runs", status_code=201)
    async def submit(request: Request) -> dict[str, str]:
        protect_workspace_command(request)
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

    return app


def _uuid_field(payload: dict[str, object], name: str) -> UUID:
    value = payload[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是规范的 UUID。")
    identifier = UUID(value)
    if str(identifier) != value:
        raise ValueError(f"{name} 必须是规范的 UUID。")
    return identifier


def _string_field(payload: dict[str, object], name: str) -> str:
    value = payload[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串。")
    return value


async def _read_object(request: Request) -> dict[str, object]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(status_code=415, detail="请使用 application/json。")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > _MAX_BODY:
            raise HTTPException(
                status_code=413, detail="编码后请求不得超过 8 MiB；原文件最多 5 MiB。"
            )
    try:
        payload: object = json.loads(content, object_pairs_hook=_unique_fields)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("请求必须是有效的 UTF-8 JSON。") from error
    return _object(payload)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("请求不能包含重复字段。")
        result[name] = value
    return result


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("需要 JSON 对象。")
    return cast(dict[str, object], value)


def _rows(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _money(value: object) -> str:
    return format(Decimal(str(value)), ",.2f")


def _percentage(value: object) -> str:
    return f"{Decimal(str(value)) * 100:.2f}%"


def _decision_text(value: object) -> str:
    if value is None:
        return "—"
    return _text(_DECISION_LABELS.get(str(value), str(value)))


def _page(
    title: str, content: str, *, csrf: str | None = None, mode: str = "历史研究 · 本机"
) -> str:
    csrf_meta = "" if csrf is None else f'<meta name="northstar-csrf" content="{_text(csrf)}">'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{csrf_meta}
<title>{_text(title)} · Northstar</title><link rel="stylesheet" href="/assets/app.css">
<script src="/assets/app.js" defer></script></head><body>
<header class="topbar"><a class="brand" href="/">NORTHSTAR<span>个人量化工作台</span></a>
<nav aria-label="工作台"><a href="/">历史研究</a><a href="/sources">来源与处理</a>
<a href="/paper">文件 Paper</a><a href="/broker">SimNow 连接</a></nav>
<span class="mode">{_text(mode)}</span></header>
<main>{content}</main><footer>研究、内部 Paper 与 SimNow 柜台证据分别保存。
当前 SimNow 仅显式只读查询；模拟结果不代表实盘表现。</footer>
</body></html>"""


def _field(
    name: str, label: str, value: object = "", *, placeholder: str = "", kind: str = "text"
) -> str:
    return (
        f'<label>{_text(label)}<input name="{_text(name)}" type="{kind}" '
        f'value="{_text(value)}" placeholder="{_text(placeholder)}" required></label>'
    )


def _spec_fields(values: dict[str, object] | None = None) -> str:
    specification = {} if values is None else values
    fields = "".join(
        _field(name, label, specification.get(name, default), placeholder=placeholder)
        for name, label, default, placeholder in (
            ("exchange", "交易所", "", "SHFE"),
            ("product", "品种", "", "RB"),
            ("symbol", "合约代码", "", "RB2605"),
            ("timezone", "交易所时区", "Asia/Shanghai", ""),
            ("currency", "币种", "CNY", ""),
            ("quantity_unit", "报价单位", "TON", ""),
            ("price_tick", "最小价格变动", "", "1"),
            ("multiplier", "每手合约乘数", "", "10"),
            ("trading_day", "交易日", "", "2026-01-07"),
            ("session_open", "时段开始（UTC）", "", "2026-01-07T01:00:00Z"),
            ("session_close", "时段结束（UTC）", "", "2026-01-07T03:30:00Z"),
            ("source_name", "数据来源标识（英文）", "", "my-market-export"),
            ("source_reference", "出处（来源地址或文件说明）", "", "数据提供方及下载地址"),
        )
    )
    options = ['<option value="">请选择实际依据</option>']
    for value, label in (
        ("SOURCE_DECLARED", "来源声明（操作人填写，未经独立验证）"),
        ("FINAL_REVISED", "最终修订数据（仅探索模拟）"),
        ("SYNTHETIC", "合成示例（非真实行情）"),
    ):
        selected = " selected" if specification.get("availability_basis") == value else ""
        options.append(f'<option value="{value}"{selected}>{label}</option>')
    return f"""<details open><summary>合约、时段与来源参数</summary>
<div class="fields">{fields}</div></details>
<label class="availability-field">历史可得时间依据<select name="availability_basis" required>
{"".join(options)}</select></label><label>可得时间说明<textarea name="availability_note" rows="3"
required placeholder="说明 available_at 的出处；最终修订数据须说明可见时间仅为假设。"
>{_text(specification.get("availability_note", ""))}</textarea></label>"""


def _source_list(sources: list[dict[str, object]]) -> str:
    rows = [
        f'<tr><td><a href="/sources/{_text(source["source_id"])}">{_text(source["filename"])}</a>'
        f'<br><span class="muted">{_text(source["source_name"])}</span></td>'
        f"<td>{_text(_INPUT_KIND_LABELS.get(str(source['input_kind']), source['input_kind']))}</td>"
        f"<td>{_text(source['byte_count'])}</td><td>{_text(source['received_at'])}</td>"
        f"<td>{_text(source['file_status'])}</td>"
        f"<td>{'允许本机下载' if source['allow_download'] else '不允许下载'}</td></tr>"
        for source in sources
    ]
    return _table(["收到的文件", "输入起点", "字节数", "接收时间", "归档状态", "下载权限"], rows)


def _attempt_list(attempts: list[dict[str, object]]) -> str:
    rows = []
    for attempt in attempts:
        snapshot = attempt["snapshot_id"]
        product = (
            "—"
            if snapshot is None
            else (f'<a href="/?dataset={_text(snapshot)}#research-form">选用已发布数据</a>')
        )
        rows.append(
            f'<tr><td><a href="/attempts/{_text(attempt["attempt_id"])}">'
            f"{_text(attempt['created_at'])}</a></td>"
            f"<td>{_text(_PROCESS_LABELS.get(str(attempt['status']), attempt['status']))}</td>"
            f"<td>{_text(_PROCESS_LABELS.get(str(attempt['stage']), attempt['stage']))}</td>"
            f'<td class="wrap-cell">{_text(attempt["error"] or "—")}</td><td>{product}</td></tr>'
        )
    return _table(["处理尝试", "结果", "已到达阶段", "原因", "发布结果"], rows)


def _sources_workspace(
    sources: list[dict[str, object]],
    attempts: list[dict[str, object]],
    rejections: list[dict[str, object]],
) -> str:
    rejected_rows = [
        f"<tr><td>{_text(item['created_at'])}</td>"
        f'<td class="wrap-cell">{_text(item["reason"])}</td>'
        f"<td>{_text(item['rejection_id'])}</td></tr>"
        for item in rejections
    ]
    return f"""<section class="intro"><p class="eyebrow">SOURCE LIBRARY</p>
<h1>原文留存，处理有据。</h1><p>查看实际收到的文件、处理失败与发布结果；不会把未发布材料列为可研究数据。</p>
<div class="actions"><a class="button" href="/#import-form">上传并处理新文件</a></div></section>
<section class="panel"><div class="section-title"><h2>托管来源</h2>
<span class="muted">最近 50 份 · 本机不可变归档</span></div>{_source_list(sources)}</section>
<section class="panel"><div class="section-title"><h2>加工与发布尝试</h2>
<span class="muted">最近 50 次 · 失败记录也保留</span></div>{_attempt_list(attempts)}</section>
<section class="panel"><h2>接收前拒绝</h2><p class="muted">仅保留有界拒绝原因和命令身份，
不保存未获准的内容；传输格式或浏览器会话校验失败不创建来源。</p>
{_table(["时间", "拒绝原因", "记录身份"], rejected_rows)}</section>"""


def _source_page(source: dict[str, object]) -> str:
    download = (
        f'<a class="button secondary" href="/api/sources/{_text(source["source_id"])}/download">'
        "按原字节下载收到的文件</a>"
        if source["allow_download"]
        else ""
    )
    upstream = source["upstream_source_id"]
    upstream_link = (
        '<p class="muted">未关联托管上游原文；不补造供应商原文或转换血缘。</p>'
        if upstream is None
        else f'<p><a href="/sources/{_text(upstream)}">查看已关联的上游来源</a></p>'
    )
    properties = (
        ("来源标识", source["source_name"]),
        ("收到的文件名", source["filename"]),
        ("输入起点", _INPUT_KIND_LABELS.get(str(source["input_kind"]), source["input_kind"])),
        ("原文字节摘要", source["content_hash"]),
        ("原文字节数", source["byte_count"]),
        ("接收时间", source["received_at"]),
        ("归档状态", source["file_status"]),
        ("用途与留存依据", source["use_basis"]),
        ("获准留存", "是" if source["allow_retention"] else "否"),
        ("本机下载", "允许" if source["allow_download"] else "不允许"),
        ("转换说明", source["transformation_note"] or "无"),
        ("来源身份", source["source_id"]),
    )
    description = "".join(f"<dt>{label}</dt><dd>{_text(value)}</dd>" for label, value in properties)
    references = _text(
        json.dumps(
            {"products": source["products"], "usages": source["usages"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return f"""<section class="intro"><a class="back" href="/sources">← 返回来源与处理</a>
<p class="eyebrow">RECEIVED SOURCE</p><h1>{_text(source["filename"])}</h1>
<p>托管的是本次实际收到的内容，不以摘要存在代替原文可读。</p>
<div class="actions">{download}</div></section>
<section class="panel"><h2>固定来源与使用声明</h2><dl class="identity">{description}</dl>
{upstream_link}<p class="muted">用途依据由操作者声明，不代表系统已核实第三方授权。
本机下载许可不等于对外再分发许可；当前不提供删除或任意服务器路径访问。</p></section>
<section class="panel"><h2>处理尝试</h2>{_attempt_list(_rows(source["attempts"]))}</section>
<section class="panel"><h2>已发布产物与运行引用</h2><p class="muted">这是当前引用关系，
不会回写历史研究的固定证据或结果身份。</p><pre>{references}</pre></section>"""


def _attempt_page(attempt: dict[str, object]) -> str:
    source = _object(attempt["source"])
    parameters = _object(attempt["parameters"])
    error = (
        ""
        if not attempt["error"]
        else f'<aside class="data-notice error">{_text(attempt["error"])}</aside>'
    )
    snapshot = attempt["snapshot_id"]
    product = (
        '<p class="muted">尚无已发布快照，不能用于研究。原文和失败证据仍保留。</p>'
        if snapshot is None
        else f'<div class="actions"><a class="button" '
        f'href="/?dataset={_text(snapshot)}#research-form">使用已发布数据继续研究</a>'
        f'<a class="button secondary" href="/datasets/{_text(snapshot)}">'
        "查看质量与数据详情</a></div>"
    )
    identities = (
        ("输入来源", source["filename"]),
        ("原文字节摘要", source["content_hash"]),
        ("处理尝试", attempt["attempt_id"]),
        ("命令身份", attempt["request_id"]),
        ("前次尝试", attempt["retry_of"] or "首次处理"),
        ("处理实现摘要", attempt["implementation_hash"]),
        ("创建时间", attempt["created_at"]),
        ("更新时间", attempt["updated_at"]),
    )
    identity_text = "".join(
        f"<dt>{label}</dt><dd>{_text(value)}</dd>" for label, value in identities
    )
    quality = _text(json.dumps(attempt["quality"], ensure_ascii=False, indent=2))
    exact_parameters = _text(json.dumps(parameters, ensure_ascii=False, indent=2))
    return f"""<section class="intro"><a class="back" href="/sources/{_text(source["source_id"])}">
← 返回来源详情</a><p class="eyebrow">PROCESSING ATTEMPT</p>
<h1>{_text(source["filename"])} · 处理尝试</h1>
<p>{_text(_PROCESS_LABELS.get(str(attempt["status"]), attempt["status"]))} ·
已到达：{_text(_PROCESS_LABELS.get(str(attempt["stage"]), attempt["stage"]))}</p>
{product}</section>{error}<section class="panel"><h2>固定输入与执行证据</h2>
<dl class="identity">{identity_text}</dl><details><summary>本次原始处理参数</summary>
<pre>{exact_parameters}</pre></details><details open><summary>已完成的质量证据</summary>
<pre>{quality}</pre></details></section><section class="panel"><h2>修正参数后重新处理</h2>
<p class="muted">读取同一份托管原文，创建新的处理尝试。
不替换原文、权限、历史可得时间声明或已发布事实。
相同内容与参数可复用已确认产物；文件内容本身错误时，请上传修正后的新文件。</p>
<form id="reprocess-form" data-source-id="{_text(source["source_id"])}">{_spec_fields(parameters)}
<button type="submit">用这些参数重新处理原文</button>
<p id="reprocess-status" class="status" role="status"></p></form></section>"""


def _lineage_panel(lineage: dict[str, object]) -> str:
    references = _text(json.dumps(lineage["usages"], ensure_ascii=False, indent=2))
    return f"""<section class="panel"><h2>托管来源与处理链</h2>
<p class="muted">以下为当前可查询的来源、处理和引用；它们不改变本次研究或快照的固定身份。</p>
{_source_list(_rows(lineage["sources"]))}{_attempt_list(_rows(lineage["attempts"]))}
<details><summary>当前运行引用</summary><pre>{references}</pre></details></section>"""


def _configuration_fields() -> str:
    defaults = ResearchConfig().to_dict()
    groups = (
        (
            "策略 · 当前动量实现",
            (
                ("lookback", "动量回看 bars"),
                ("threshold", "动量阈值（小数）"),
                ("target_fraction", "目标仓位比例"),
            ),
        ),
        (
            "Risk · 目标与授权限制",
            (
                ("max_lots", "最大持仓手数"),
                ("max_gross_notional", "最大名义敞口"),
                ("max_margin_fraction", "保证金 / 权益上限"),
                ("max_adverse_price_move_fraction", "授权不利价格变动上限"),
                ("order_lifetime_seconds", "授权有效时间（秒）"),
            ),
        ),
        (
            "内部模拟账户与成交假设",
            (
                ("initial_cash", "独立模拟账户初始资金"),
                ("initial_margin_fraction", "初始保证金比例"),
                ("fee_per_lot", "每手每次成交费用"),
                ("slippage_ticks", "每次成交滑点 ticks"),
            ),
        ),
    )
    return "".join(
        f'<fieldset><legend>{title}</legend><div class="fields">'
        + "".join(_field(name, label, defaults[name]) for name, label in fields)
        + "</div></fieldset>"
        for title, fields in groups
    )


def _paper_workspace(
    configurations: list[dict[str, object]],
    datasets: list[dict[str, object]],
    sessions: list[dict[str, object]],
) -> str:
    configuration_options = "".join(
        f'<option value="{_text(item["configuration_id"])}">'
        f"{_text(item['name'])} · {_text(item['created_at'])} · "
        f"{_text(str(item['configuration_id'])[:12])}</option>"
        for item in configurations
    )
    dataset_options = "".join(
        f'<option value="{_text(item["snapshot_id"])}">{_text(_dataset_label(item))}</option>'
        for item in datasets
    )
    session_rows = []
    for session in sessions:
        summary = _object(session["summary"])
        market = _object(session["market"])
        configuration = _object(session["configuration"])
        session_rows.append(
            f'<tr><td><a href="/paper/{_text(session["session_id"])}">'
            f"{_text(market['symbol'])} · {_text(session['created_at'])}</a></td>"
            f"<td>{_text(configuration['name'])}</td>"
            f"<td>{_paper_status(session)}</td><td>文件输入 · 内部 Paper</td>"
            f"<td>{_text(session['cursor'])} / {_text(session['total_inputs'])}</td>"
            f"<td>{_money(summary['ending_equity'])} {_text(market['currency'])}</td>"
            f"<td>{_text(summary['ending_position_lots'])} 手</td></tr>"
        )
    sessions_table = (
        _table(["会话", "固定配置", "状态", "输入类型", "已处理", "权益", "持仓"], session_rows)
        if session_rows
        else '<p class="empty">还没有 Paper 会话。保存配置后，选择已接受的数据创建。</p>'
    )
    return f"""<section class="intro"><p class="eyebrow">FILE PAPER WORKSPACE</p>
<h1>一条输入，一次可核对的推进。</h1>
<p>保存不可变配置，用已接受的数据创建独立内部 Paper 会话；刷新或重启后仍可查看进度。</p></section>
<aside class="data-notice"><strong>文件输入演示 · 不是持续行情，也不是柜台仿真或实盘</strong>
<p>只在明确点击时处理下一条已保存行情。没有后台自动推进，不连接行情来源或交易账户。
每个新会话使用独立的模拟账户，不能用它替换、重置或控制其他账户。</p></aside>
<div class="workspace"><section class="panel"><div class="section-title">
<span class="step">01</span><h2>保存策略与 Risk 配置</h2></div>
<p class="muted">修改后保存新修订；不会改变已经创建的会话，也不会授予任何实盘执行权限。</p>
<form id="configuration-form">{_field("name", "配置名称", placeholder="例如：RB 单时段动量")}
{_configuration_fields()}
<p class="muted">资金、保证金、费用与滑点均为内部模拟假设，不代表已核实的柜台条款。</p>
<button type="submit">保存不可变配置</button>
<p id="configuration-status" class="status" role="status"></p></form></section>
<section class="panel"><div class="section-title"><span class="step">02</span>
<h2>创建独立 Paper 会话</h2></div>
<form id="paper-create-form"><label>固定配置修订<select name="configuration_id" required>
<option value="">请选择已保存的配置</option>{configuration_options}</select></label>
<div id="configuration-preview" class="configuration-preview" aria-live="polite"></div>
<label>已接受的文件数据<select name="snapshot_id" required>
<option value="">请选择已接受的数据</option>{dataset_options}</select></label>
<div id="paper-data-notice"></div>
<p class="muted">没有数据？<a href="/">先导入并通过质量检查</a>。创建时固定本次数据、
配置与实现身份，不绑定可变的“最新版”。会话从暂停状态开始，不热换配置。</p>
<button type="submit">创建独立会话（默认暂停）</button>
<p id="paper-create-status" class="status" role="status"></p></form></section></div>
<section class="panel"><div class="section-title"><h2>内部 Paper 会话</h2>
<span class="muted">各自独立账户 · 持久保存进度</span></div>{sessions_table}</section>"""


def _paper_status(session: dict[str, object]) -> str:
    return "输入已完成" if session["status"] == "COMPLETED" else "暂停 · 等待明确推进"


def _paper_report(session: dict[str, object]) -> str:
    configuration = _object(session["configuration"])
    summary = _object(session["summary"])
    market = _object(session["market"])
    snapshot = _object(session["snapshot"])
    data_notice = _availability_notice(_object(session["data"]))
    currency = _text(market["currency"])
    config_text = _text(json.dumps(configuration["config"], ensure_ascii=False, indent=2))
    pending = session["pending_order"]
    pending_text = (
        '<p class="muted">无待成交授权。</p>'
        if pending is None
        else "<p>已授权但尚未成交；下一条输入仍须满足有效期和价格限制，不能视为已成交。</p>"
        f"<pre>{_text(json.dumps(pending, ensure_ascii=False, indent=2))}</pre>"
    )
    limitations = "".join(
        f"<li>{_text(item)}</li>" for item in cast(list[str], session["limitations"])
    )
    blocked = session["blocked_reason"]
    blocked_notice = (
        f'<p class="status error">无法推进：{_text(blocked)}</p>' if blocked is not None else ""
    )
    disabled = "" if session["can_advance"] else " disabled"
    metrics = "".join(
        f'<div class="metric"><span>{label}</span><strong>{value}</strong>'
        f"<small>{note}</small></div>"
        for label, value, note in (
            ("模拟账户权益", _money(summary["ending_equity"]), currency),
            ("累计模拟费用", _money(summary["total_fees"]), currency),
            ("当前持仓", _text(summary["ending_position_lots"]), "手 · 不虚构期末平仓"),
            (
                "已处理文件输入",
                f"{_text(session['cursor'])} / {_text(session['total_inputs'])}",
                f"剩余 {_text(session['remaining_inputs'])} 条",
            ),
        )
    )
    last_event = session["last_event"]
    last_text = (
        "尚未处理任何输入；账户为初始资金，策略尚未预热。"
        if last_event is None
        else f"最后已提交观测：{_object(last_event)['at']}"
    )
    fill_rows = [
        "<tr>"
        + "".join(
            f"<td>{_text(fill[name])}</td>"
            for name in (
                "filled_at",
                "side",
                "quantity_lots",
                "price",
                "fee",
                "realized_pnl",
                "position_lots",
            )
        )
        + "</tr>"
        for fill in _rows(session["fills"])[-200:]
    ]
    decision_rows = [
        "<tr>"
        + "".join(
            f"<td>{_decision_cell(name, decision[name])}</td>"
            for name in (
                "at",
                "momentum",
                "target_fraction",
                "outcome",
                "reason",
                "approved_position_lots",
            )
        )
        + "</tr>"
        for decision in _rows(session["decisions"])[-200:]
    ]
    fills_table = _table(
        ["成交时间（UTC）", "方向", "手数", "成交价", "费用", "已实现损益", "成交后持仓"], fill_rows
    )
    decisions_table = _table(
        ["决策时间（UTC）", "动量", "目标比例", "结果", "原因", "批准持仓"], decision_rows
    )
    identities = (
        ("输入类型", session["input_type"]),
        ("固定配置名称", configuration["name"]),
        ("固定配置身份", configuration["configuration_id"]),
        ("策略配置摘要", configuration["strategy_hash"]),
        ("Risk 配置摘要", configuration["risk_hash"]),
        ("实现与依赖摘要", session["implementation_hash"]),
        ("数据快照", snapshot["id"]),
        ("数据摘要", snapshot["content_hash"]),
        ("独立模拟账户", session["account_id"]),
        ("会话身份", session["session_id"]),
        ("最后提交时间", session["updated_at"]),
    )
    identity_text = "".join(
        f"<dt>{label}</dt><dd>{_text(value)}</dd>" for label, value in identities
    )
    return f"""<section class="intro report-intro">
<a class="back" href="/paper">← 返回 Paper 工作台</a>
<p class="eyebrow">INTERNAL PAPER · FILE REPLAY</p><h1>{_text(market["symbol"])} · 文件 Paper</h1>
<p>{_paper_status(session)} · 固定配置：{_text(configuration["name"])} ·
{_text(session["created_at"])}</p>
<div class="actions"><button id="paper-advance"
data-session-id="{_text(session["session_id"])}"{disabled}>核对并推进下一条</button>
<a class="button secondary" href="/api/paper/{_text(session["session_id"])}"
download="paper-{_text(session["session_id"])}.json">下载会话证据</a>
<a class="button secondary" href="/datasets/{_text(snapshot["id"])}">查看来源与质量</a></div>
<p id="paper-advance-status" class="status" role="status"></p>{blocked_notice}</section>
<aside class="data-notice"><strong>内部模拟账户 · 已接受文件逐条回放</strong>
<p>不连接持续行情或柜台，不会发送真实委托。每次明确操作最多推进一条，提交后仍暂停。
“输入已完成”仅表示文件耗尽，不表示已平仓；残余持仓和待单继续可见。</p><ul>{limitations}</ul></aside>
{data_notice}
<section class="metrics">{metrics}</section>
<section class="panel"><div class="section-title"><h2>持久进度与权益</h2>
<span class="muted">{_text(last_text)}</span></div>{_equity_chart(_rows(session["equity_curve"]))}
<div class="account-line"><span>初始模拟资金 {_money(summary["initial_cash"])} {currency}</span>
<span>当前账本现金 {_money(summary["ending_cash"])} {currency}</span>
<span>已实现损益 {_money(summary["realized_pnl"])}</span>
<span>未实现损益 {_money(summary["unrealized_pnl"])}</span></div></section>
<section class="panel"><h2>待成交授权</h2>{pending_text}</section>
<section class="panel"><h2>模拟成交与持仓</h2>{fills_table}</section>
<section class="panel"><h2>策略与风险决定</h2>{decisions_table}</section>
<section class="panel"><h2>实际生效的固定绑定</h2><p class="muted">此会话不可更换配置。
编辑、另存配置不会改变这个账户的资金、持仓、风险记录或进度。新建会话是另一个独立模拟账户。</p>
<details><summary>查看完整配置与身份</summary><pre>{config_text}</pre>
<dl class="identity">{identity_text}</dl></details></section>"""


def _workspace(
    runs: list[dict[str, object]],
    datasets: list[dict[str, object]],
    selected: dict[str, object] | None,
) -> str:
    defaults = ResearchConfig().to_dict()
    basic_fields = "".join(
        _field(name, label, defaults[name])
        for name, label in (
            ("initial_cash", "初始资金"),
            ("lookback", "动量回看 bars"),
            ("threshold", "动量阈值（小数）"),
            ("target_fraction", "目标仓位比例"),
            ("max_lots", "最大持仓手数"),
            ("fee_per_lot", "每手每次成交费用"),
            ("slippage_ticks", "每次成交滑点 ticks"),
        )
    )
    risk_fields = "".join(
        _field(name, label, defaults[name])
        for name, label in (
            ("max_gross_notional", "最大名义敞口"),
            ("max_margin_fraction", "保证金 / 权益上限"),
            ("initial_margin_fraction", "初始保证金比例"),
            ("max_adverse_price_move_fraction", "授权不利价格变动上限"),
            ("order_lifetime_seconds", "授权有效时间（秒）"),
        )
    )
    options = ['<option value="">请选择已接受的数据</option>']
    for dataset in datasets:
        selection = (
            " selected"
            if selected is not None and (dataset["snapshot_id"] == selected["snapshot_id"])
            else ""
        )
        label = _dataset_label(dataset)
        options.append(
            f'<option value="{_text(dataset["snapshot_id"])}"{selection}>{_text(label)}</option>'
        )
    snapshot_field = (
        '<label>选择行情数据<select name="snapshot_id" required>'
        + "".join(options)
        + "</select></label>"
    )
    selected_link = (
        '<a id="selected-data-link" hidden>查看来源与质量详情</a>'
        if selected is None
        else f'<a id="selected-data-link" href="/datasets/{_text(selected["snapshot_id"])}">'
        "查看来源与质量详情</a>"
    )
    reuse_hidden = " hidden" if selected is None else ""
    selected_notice = "" if selected is None else _availability_notice(selected)
    return f"""<section class="intro"><p class="eyebrow">RESEARCH WORKSPACE</p>
<h1>让一段行情，成为可复核的研究。</h1><p>选择已保存的行情或导入新文件，查看扣费后的权益、持仓和每次风险决定。</p></section>
<section class="panel data-library"><div class="section-title"><h2>已接受的数据</h2>
<span class="muted">独立保存 · 无需先运行研究 · 最近 50 份</span></div>
{_dataset_list(datasets)}</section>
<div class="workspace"><section class="panel"><div class="section-title">
<span class="step">01</span><h2>上传原文并处理</h2></div>
<p class="muted">当前支持一个合约、一个交易日内的一个连续时段，1 分钟 bars。
填写实际合约属性和数据覆盖时段。</p>
<form id="import-form"><label class="file-input">CSV 文件
<input name="file" type="file" accept=".csv,text/csv" required></label>
<p class="muted">原文件最多 5 MiB，按收到的字节留存，不在浏览器中解码或转换。
数据层另检查归档总量和可用磁盘空间；处理失败也能在<a href="/sources">来源与处理</a>查询。</p>
<fieldset><legend>输入起点与明确许可</legend>
<label>实际上传的内容<select name="input_kind" required>
<option value="">请选择文件的真实起点</option>
<option value="RECEIVED_CSV">实际收到的 CSV（不宣称供应商原文）</option>
<option value="CONVERTED_CSV">外部转换后 CSV</option></select></label>
<label>用途与留存依据<textarea name="use_basis" rows="3" required
placeholder="写明来源条款或授权，以及本机研究、留存与备份用途。请勿上传无权留存的内容。"></textarea></label>
<label class="check-field"><input type="checkbox" name="allow_retention" required>
我确认有权将本次内容留存于应用归档，并用于本机研究及备份。</label>
<label class="check-field"><input type="checkbox" name="allow_download">
允许本机操作者下载此归档文件（不等于对外再分发许可）。</label>
<details><summary>已有上游归档与转换说明（可选）</summary>
<label>已托管上游来源身份<input name="upstream_source_id"
placeholder="已有来源详情中的 UUID"></label>
<label>转换说明<textarea name="transformation_note" rows="3"
placeholder="外部转换文件需说明转换方式；没有上游原文时明确缺失，不能补造血缘。"></textarea></label>
</details></fieldset>{_spec_fields()}
<details><summary>CSV 格式</summary><p>UTF-8，第一行包含以下列。
时间带明确时区；available_at 不得早于该分钟完成。</p>
<code class="csv-columns">{_CSV_COLUMNS}</code>
<p class="muted">一行对应一个完整分钟；volume 为手数。
合约、币种与时段使用上方填写的属性。</p></details>
<button type="submit">按原字节归档并处理</button>
<p id="import-status" role="status" class="status"></p></form></section>
<section class="panel"><div class="section-title">
<span class="step">02</span><h2>运行研究</h2></div>
<p class="muted">系统自动取得合约属性，并从模拟账本生成账户状态。
动量超过阈值后映射为明确的目标仓位。</p>
<form id="research-form">{snapshot_field}
<div class="data-selection-actions">{selected_link}
<button type="button" class="text-button" id="reuse-data-metadata"{reuse_hidden}>
复用合约与来源信息</button></div>
<div id="selected-data-notice">{selected_notice}</div>
<div class="fields">{basic_fields}</div><details><summary>风险限制</summary>
<div class="fields">{risk_fields}</div></details>
<button type="submit">运行整段研究</button>
<p id="research-status" role="status" class="status"></p></form></section></div>
<section class="panel history"><div class="section-title"><h2>研究记录</h2>
<span class="muted">保存完整配置与结果</span></div>
{_run_list(runs)}</section>"""


def _dataset_label(data: dict[str, object]) -> str:
    return (
        f"{data['exchange']} · {data['symbol']} · {data['trading_day']} · "
        f"{data['session_open']} — {data['session_close']} · {data['bar_count']} bars"
    )


def _dataset_list(datasets: list[dict[str, object]]) -> str:
    if not datasets:
        return (
            '<div class="empty">还没有可研究的数据。导入并通过质量检查后，'
            "即使不运行研究，也会保存在这里。</div>"
        )
    rows = [
        f'<tr><td><a href="/datasets/{_text(data["snapshot_id"])}">'
        f"{_text(data['exchange'])} · {_text(data['symbol'])}</a></td>"
        f"<td>{_text(data['trading_day'])}</td>"
        f"<td>{_text(data['session_open'])}<br>{_text(data['session_close'])}</td>"
        f"<td>{_text(data['bar_count'])}</td>"
        f'<td><a href="/?dataset={_text(data["snapshot_id"])}#research-form">'
        "选用此数据</a></td></tr>"
        for data in datasets
    ]
    return _table(["实际合约", "交易日", "覆盖时段（UTC）", "Bars", ""], rows)


def _availability_notice(data: dict[str, object]) -> str:
    notices = {
        "FINAL_REVISED": (
            "最终修订数据 · 仅用于探索模拟",
            "信息时钟假设为每根 bar 完成时可见，并非历史上观测到的首次可得时间。"
            "不能据此证明当时能够做出相同决策。",
        ),
        "SOURCE_DECLARED": (
            "来源声明 · 未经独立验证",
            "available_at 依据由操作人声明，系统未独立验证它是否为历史首次可得时间。",
        ),
        "SYNTHETIC": (
            "合成示例 · 非真实行情",
            "仅用于演示和工程验证，不用于评价真实市场中的策略表现。",
        ),
    }
    title, note = notices[str(data["availability_basis"])]
    return (
        f'<aside class="data-notice"><strong>{title}</strong><p>{note}</p>'
        f'<p class="muted">声明：{_text(data["availability_note"])}</p></aside>'
    )


def _dataset_page(data: dict[str, object]) -> str:
    return f"""<section class="intro report-intro"><a class="back" href="/">← 返回工作台</a>
<p class="eyebrow">ACCEPTED DATA</p><h1>{_text(data["symbol"])} · 数据详情</h1>
<p>{_text(data["trading_day"])} · {_text(data["bar_count"])} 个 bars · 接受时的固定证据</p>
<div class="actions"><a class="button"
href="/?dataset={_text(data["snapshot_id"])}#research-form">用此数据研究</a>
<a class="button secondary" href="/api/datasets/{_text(data["snapshot_id"])}"
download="dataset-{_text(data["snapshot_id"])}.json">下载数据说明</a></div></section>
{_availability_notice(data)}{_data_evidence(data)}"""


def _data_evidence(data: dict[str, object]) -> str:
    semantics, quality = _object(data["semantics"]), _object(data["quality"])
    minute = _object(quality["minute"])
    properties: tuple[tuple[str, object], ...] = (
        ("实际合约", f"{data['exchange']} / {data['product']} / {data['symbol']}"),
        ("交易日", data["trading_day"]),
        ("覆盖时段（UTC）", f"{data['session_open']} — {data['session_close']}"),
        ("时间语义", f"{semantics['interval_seconds']} 秒 / {semantics['timestamp_convention']}"),
        ("可得时间截点", semantics["available_at_cutoff"]),
        ("时区 / 币种", f"{semantics['timezone']} / {semantics['currency']}"),
        ("报价单位 / 成交量单位", f"{semantics['quantity_unit']} / {semantics['volume_unit']}"),
        ("最小价格变动 / 每手乘数", f"{semantics['price_tick']} / {semantics['multiplier']}"),
        ("价格调整方式", semantics["adjustment"]),
        ("来源出处", data["source_reference"]),
        ("数据快照", data["snapshot_id"]),
        ("数据摘要", data["content_hash"]),
        ("接受发布时间", data["published_at"]),
    )
    description = "".join(f"<dt>{label}</dt><dd>{_text(value)}</dd>" for label, value in properties)
    source_rows = [
        "<tr>"
        + "".join(
            f"<td>{_text(source[key])}</td>"
            for key in (
                "source_name",
                "received_at",
                "byte_count",
                "acquisition_use",
                "redistribution_policy",
                "retention_policy",
            )
        )
        + "</tr>"
        for source in _rows(data["sources"])
    ]
    import_rows = [
        "<tr>"
        + "".join(
            f"<td>{_text(item[key])}</td>"
            for key in (
                "outcome",
                "delivery_gate",
                "rows_read",
                "rows_accepted",
                "rows_rejected",
                "rows_inserted",
                "rows_duplicate_identical",
                "rows_conflicted",
            )
        )
        + "</tr>"
        for item in _rows(quality["imports"])
    ]
    quality_detail = "".join(
        f"<dt>{label}</dt><dd>{_text(minute[key])}</dd>"
        for label, key in (
            ("分钟质量结果", "outcome"),
            ("交付门禁", "delivery_gate"),
            ("应有观测数", "expected_observation_count"),
            ("实际观测数", "observed_count"),
            ("缺失观测数", "missing_observation_count"),
        )
    )
    identities = {
        "sources": data["sources"],
        "quality": data["quality"],
        "import_spec": data["import_spec"],
    }
    exact_evidence = escape(json.dumps(identities, ensure_ascii=False, sort_keys=True, indent=2))
    limitations = "".join(
        f"<li>{_text(note)}</li>" for note in cast(list[str], data["limitations"])
    )
    sources_table = _table(
        ["来源", "原始文件接收时间", "字节数", "使用依据", "再分发", "保留策略"], source_rows
    )
    return f"""<section class="panel"><h2>行情来源与数据含义</h2>
<dl class="identity">{description}</dl>{sources_table}</section>
<section class="panel"><h2>接受时的质量结果</h2><dl class="identity">{quality_detail}</dl>
{_table(["导入质量", "门禁", "读取", "接受", "拒绝", "新写入", "相同重复", "冲突"], import_rows)}
<details><summary>原始文件摘要、质量评估身份与导入声明</summary><pre>{exact_evidence}</pre></details></section>
<section class="panel"><h2>数据使用限制</h2><ul>{limitations}</ul></section>"""


def _run_list(runs: list[dict[str, object]]) -> str:
    if not runs:
        return '<div class="empty">还没有研究记录。导入一段行情并运行后，结果会保存在这里。</div>'
    rows = []
    for run in runs:
        summary, market = _object(run["summary"]), _object(run["market"])
        rows.append(
            f'<tr><td><a href="/runs/{_text(run["run_id"])}">{_text(market["symbol"])}</a></td>'
            f"<td>{_text(run['created_at'])}</td><td>{_text(summary['bar_count'])}</td>"
            f"<td>{_percentage(summary['total_return'])}</td>"
            f"<td>{_percentage(summary['max_drawdown_fraction'])}</td>"
            f"<td>{_text(summary['fill_count'])}</td>"
            f'<td><button class="text-button" data-use-run="{_text(run["run_id"])}">'
            "用此配置</button></td></tr>"
        )
    return _table(["合约", "保存时间（UTC）", "Bars", "净收益率", "最大回撤", "成交", ""], rows)


def _table(headers: list[str], rows: list[str]) -> str:
    headings = "".join(f"<th>{_text(header)}</th>" for header in headers)
    body = (
        "".join(rows)
        if rows
        else f'<tr><td colspan="{len(headers)}" class="empty">无记录</td></tr>'
    )
    return (
        f'<div class="table-scroll"><table><thead><tr>{headings}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _report(run: dict[str, object]) -> str:
    result = _object(run["result"])
    configuration = _object(run["config"])
    data = None if result["data"] is None else _object(result["data"])
    data_notice = "" if data is None else _availability_notice(data)
    data_evidence = "" if data is None else _data_evidence(data)
    market, summary = _object(result["market"]), _object(result["summary"])
    curve, fills, decisions = (
        _rows(result["equity_curve"]),
        _rows(result["fills"]),
        _rows(result["decisions"]),
    )
    currency = _text(market["currency"])
    metrics = "".join(
        f'<div class="metric"><span>{label}</span><strong>{value}</strong>'
        f"<small>{detail}</small></div>"
        for label, value, detail in (
            ("期末权益", _money(summary["ending_equity"]), currency),
            ("净收益率", _percentage(summary["total_return"]), "已扣成交费用与滑点"),
            (
                "最大回撤",
                _percentage(summary["max_drawdown_fraction"]),
                f"{_money(summary['max_drawdown'])} {currency}",
            ),
            ("成交费用", _money(summary["total_fees"]), f"{summary['fill_count']} 次成交"),
        )
    )
    fill_rows = [
        "<tr>"
        + "".join(
            f"<td>{_decision_text(fill[key])}</td>"
            for key in (
                "filled_at",
                "side",
                "quantity_lots",
                "price",
                "fee",
                "realized_pnl",
                "position_lots",
            )
        )
        + "</tr>"
        for fill in fills[-200:]
    ]
    decision_rows = [
        "<tr>"
        + "".join(
            f"<td>{_decision_cell(key, decision[key])}</td>"
            for key in (
                "at",
                "momentum",
                "target_fraction",
                "outcome",
                "reason",
                "approved_position_lots",
            )
        )
        + "</tr>"
        for decision in decisions[-200:]
    ]
    assumptions = "".join(
        f"<li>{_text(item)}</li>" for item in cast(list[str], result["assumptions"])
    )
    config = escape(json.dumps(run["config"], ensure_ascii=False, sort_keys=True, indent=2))
    pending = (
        "无待成交授权。"
        if result["pending_order"] is None
        else "期末仍有未成交授权，未计为成交。完整信息见下载结果。"
    )
    fills_table = _table(
        ["成交时间（UTC）", "方向", "手数", "成交价", "费用", "已实现损益", "成交后持仓"], fill_rows
    )
    decisions_table = _table(
        ["决策时间（UTC）", "动量", "目标比例", "结果", "原因", "批准持仓"], decision_rows
    )
    return f"""<section class="intro report-intro"><a class="back" href="/">← 返回工作台</a>
<p class="eyebrow">RESEARCH REPORT</p><h1>{_text(market["symbol"])} · 研究结果</h1>
<p>{_text(summary["bar_count"])} 个 bars · {_text(summary["decision_count"])} 次决策 ·
{_text(run["created_at"])}</p>
<div class="actions"><button data-rerun="{_text(run["run_id"])}">重跑相同数据与配置</button>
<a class="button secondary" href="/api/runs/{_text(run["run_id"])}"
download="research-{_text(run["run_id"])}.json">下载完整结果</a></div>
<p id="research-status" role="status" class="status"></p></section>
{data_notice}
<section class="metrics">{metrics}</section>
<section class="panel"><div class="section-title"><h2>权益变化</h2>
<span class="muted">{currency} · 按观测顺序</span></div>
{_equity_chart(curve)}<div class="account-line">
<span>已实现损益 {_money(summary["realized_pnl"])}</span>
<span>未实现损益 {_money(summary["unrealized_pnl"])}</span>
<span>期末持仓 {_text(summary["ending_position_lots"])} 手</span></div>
<p class="muted">{pending} 期末持仓按最后可见价格估值，未虚构平仓。</p></section>
<section class="panel"><div class="section-title"><h2>成交与持仓</h2>
<span class="muted">最近 200 条 / 共 {len(fills)} 条</span></div>{fills_table}</section>
<section class="panel"><div class="section-title"><h2>策略与风险决定</h2>
<span class="muted">最近 200 条 / 共 {len(decisions)} 条</span></div>{decisions_table}</section>
{data_evidence}
<section class="panel"><h2>模型范围</h2><p>这是单合约、单交易日的历史模拟；
成交使用完成的 bar 价格。样本内结果不能证明策略可盈利。</p>
<p>本次模拟按每手每次成交收取 {_money(configuration["fee_per_lot"])} {currency}，
每次成交施加 {_text(configuration["slippage_ticks"])} ticks 的不利滑点，
初始保证金比例为 {_percentage(configuration["initial_margin_fraction"])}。
这些是研究配置中的模拟假设，并非已核实的交易所或期货公司实际条款。</p>
<ul>{assumptions}</ul></section><section class="panel"><details>
<summary>完整配置与可复核身份</summary><pre>{config}</pre><dl class="identity">
<dt>数据快照</dt><dd>{_text(_object(run["snapshot"])["id"])}</dd>
<dt>数据摘要</dt><dd>{_text(_object(run["snapshot"])["content_hash"])}</dd>
<dt>实现与依赖摘要</dt><dd>{_text(run["implementation_hash"])}</dd>
<dt>计算结果摘要</dt><dd>{_text(result["result_hash"])}</dd><dt>研究记录</dt><dd>{_text(run["run_id"])}</dd></dl>
</details></section>"""


def _equity_chart(curve: list[dict[str, object]]) -> str:
    if not curve:
        return '<p class="empty">没有可绘制的观测。</p>'
    values = [Decimal(str(point["equity"])) for point in curve]
    low, high = min(values), max(values)
    span = high - low
    selected = {0, len(values) - 1}
    # Preserve each bucket's extremes instead of hiding drawdowns by taking
    # every nth sample. The full unabridged series remains in the stored result.
    bucket = max(1, (len(values) + 399) // 400)
    for start in range(0, len(values), bucket):
        indexes = range(start, min(start + bucket, len(values)))
        selected.add(min(indexes, key=values.__getitem__))
        selected.add(max(indexes, key=values.__getitem__))
    coordinates = []
    for index in sorted(selected):
        x = Decimal(32) + Decimal(index) * 936 / max(1, len(values) - 1)
        y = Decimal(120) if span == 0 else Decimal(220) - (values[index] - low) * 196 / span
        coordinates.append(f"{x:.2f},{y:.2f}")
    return (
        '<svg class="equity-chart" viewBox="0 0 1000 260" role="img" aria-label="扣费后的权益曲线">'
        '<line x1="32" y1="220" x2="968" y2="220" class="grid-line"/>'
        f'<polyline points="{" ".join(coordinates)}" class="equity-line"/>'
        f'<text x="32" y="16">{_money(high)}</text>'
        f'<text x="32" y="236">{_money(low)}</text>'
        f'<text x="32" y="254">{_text(curve[0]["at"])}</text>'
        f'<text x="968" y="254" text-anchor="end">{_text(curve[-1]["at"])}</text></svg>'
    )


def _decision_cell(name: str, value: object) -> str:
    if name == "momentum" and value is not None:
        return f"{Decimal(str(value)) * 100:.4f}%"
    if name == "target_fraction":
        return _percentage(value)
    return _decision_text(value)
