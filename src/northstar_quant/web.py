"""A local, same-origin workspace for importing data and inspecting research."""

from __future__ import annotations

import json
import re
import tempfile
from decimal import Decimal
from html import escape
from importlib.resources import files
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint

from northstar_quant.data.research import ImportSpec, import_csv, load_dataset
from northstar_quant.research import ResearchConfig, run_research
from northstar_quant.runs import RunStore

_MAX_BODY = 5 * 1024 * 1024
_LOCAL_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::[1-9][0-9]{0,4})?\Z")
_CSV_COLUMNS = "event_time,available_at,source_record_id,open,high,low,close,volume"
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

    return create_app(open_database())


def create_app(engine: Engine) -> FastAPI:
    store = RunStore(engine)
    app = FastAPI(title="Northstar · 个人量化研究", docs_url=None, redoc_url=None)

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

    @app.exception_handler(LookupError)
    async def missing_run(_request: Request, _error: LookupError) -> JSONResponse:
        return JSONResponse({"detail": "没有找到这次研究。"}, status_code=404)

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
    def home() -> str:
        return _page("研究工作台", _workspace(store.list()))

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

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        return store.get(run_id)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def show_run(run_id: str) -> str:
        return _page("研究结果", _report(store.get(run_id)))

    @app.post("/api/import")
    async def upload(request: Request) -> dict[str, object]:
        payload = await _read_object(request)
        if set(payload) != {"csv", "spec"}:
            raise ValueError("导入需要 csv 和 spec，不能含其他字段。")
        csv = payload["csv"]
        if not isinstance(csv, str) or not csv.strip():
            raise ValueError("请选择包含数据的 UTF-8 CSV 文件。")
        spec = ImportSpec.from_mapping(_object(payload["spec"]))

        def accept_file() -> dict[str, object]:
            path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(prefix="northstar-import-", suffix=".csv") as file:
                    path = Path(file.name)
                    file.write(csv.encode("utf-8"))
                    file.flush()
                    dataset = import_csv(engine, path, spec)
                    return {
                        "snapshot_id": str(dataset.snapshot_id),
                        "content_hash": dataset.content_hash,
                        "bar_count": len(dataset.bars),
                    }
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)

        return await run_in_threadpool(accept_file)

    @app.post("/api/runs", status_code=201)
    async def submit(request: Request) -> dict[str, str]:
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
            dataset = load_dataset(engine, snapshot_id)
            result = run_research(dataset, configuration)
            run_id = store.save(dataset, configuration, result)
            return {"run_id": run_id, "url": f"/runs/{run_id}"}

        return await run_in_threadpool(execute)

    return app


async def _read_object(request: Request) -> dict[str, object]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(status_code=415, detail="请使用 application/json。")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > _MAX_BODY:
            raise HTTPException(status_code=413, detail="请求不得超过 5 MiB。")
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


def _page(title: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_text(title)} · Northstar</title><link rel="stylesheet" href="/assets/app.css">
<script src="/assets/app.js" defer></script></head><body>
<header class="topbar"><a class="brand" href="/">NORTHSTAR<span>个人量化研究</span></a>
<span class="mode">历史研究 · 本机</span></header>
<main>{content}</main><footer>数据、决策、成交与账本均来自本机实际运行。研究结果不代表实盘表现。</footer>
</body></html>"""


def _field(
    name: str, label: str, value: object = "", *, placeholder: str = "", kind: str = "text"
) -> str:
    return (
        f'<label>{_text(label)}<input name="{_text(name)}" type="{kind}" '
        f'value="{_text(value)}" placeholder="{_text(placeholder)}" required></label>'
    )


def _workspace(runs: list[dict[str, object]]) -> str:
    fields = "".join(
        _field(name, label, value, placeholder=placeholder)
        for name, label, value, placeholder in (
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
        )
    )
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
    snapshot_field = _field(
        "snapshot_id", "数据快照 ID", placeholder="导入后自动填入，或选择已有快照"
    )
    return f"""<section class="intro"><p class="eyebrow">RESEARCH WORKSPACE</p>
<h1>让一段行情，成为可复核的研究。</h1><p>导入真实分钟数据，运行整段策略，查看扣费后的权益、持仓和每次风险决定。</p></section>
<div class="workspace"><section class="panel"><div class="section-title">
<span class="step">01</span><h2>导入行情</h2></div>
<p class="muted">当前支持一个合约、一个交易日内的一个连续时段，1 分钟 bars。
填写实际合约属性和数据覆盖时段。</p>
<form id="import-form"><label class="file-input">CSV 文件
<input name="file" type="file" accept=".csv,text/csv" required></label>
<details open><summary>合约与时段</summary><div class="fields">{fields}</div></details>
<details><summary>CSV 格式</summary><p>UTF-8，第一行包含以下列。
时间带明确时区；available_at 不得早于该分钟完成。</p>
<code class="csv-columns">{_CSV_COLUMNS}</code>
<p class="muted">一行对应一个完整分钟；volume 为手数。
合约、币种与时段使用上方填写的属性。</p></details>
<button type="submit">导入并检查数据</button>
<p id="import-status" role="status" class="status"></p></form></section>
<section class="panel"><div class="section-title">
<span class="step">02</span><h2>运行研究</h2></div>
<p class="muted">系统自动取得合约属性，并从模拟账本生成账户状态。
动量超过阈值后映射为明确的目标仓位。</p>
<form id="research-form">{snapshot_field}
<div class="fields">{basic_fields}</div><details><summary>风险限制</summary>
<div class="fields">{risk_fields}</div></details>
<button type="submit">运行整段研究</button>
<p id="research-status" role="status" class="status"></p></form></section></div>
<section class="panel history"><div class="section-title"><h2>研究记录</h2>
<span class="muted">保存完整配置与结果</span></div>
{_run_list(runs)}</section>"""


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
<section class="panel"><h2>模型范围</h2><p>这是单合约、单交易日的历史模拟；
成交使用完成的 bar 价格。样本内结果不能证明策略可盈利。</p>
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
