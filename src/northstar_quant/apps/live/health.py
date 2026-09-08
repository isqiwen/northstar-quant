"""Live Web displays the owning Live's observations, never its own disk as Live's."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from northstar_quant.live import LiveClient, RuntimeUnavailable
from northstar_quant.web.html import _table, _text, workspace_page
from northstar_quant.web_access import WorkspaceAccess


def register(app: FastAPI, access: WorkspaceAccess, live: LiveClient) -> None:
    @app.get("/live", response_class=HTMLResponse)
    async def health(request: Request) -> HTMLResponse:
        try:
            observation = await run_in_threadpool(live.diagnostics)
            runtime = observation["live_runtime"]
            storage = observation["source_filesystem"]
            fields = {
                "本次检查": observation["status"],
                "Live 运行身份": runtime["runtime_id"],
                "观察时间（非自动刷新）": observation["observed_at"],
                "检查耗时（毫秒）": observation["duration_ms"],
                "PostgreSQL 可达性": observation["database"]["status"],
                "PostgreSQL 所在磁盘容量": "未知（未测量，不能用来源盘代替）",
                "Live 来源文件系统": storage["status"],
                "可用字节": storage.get("free_bytes"),
                "保留空闲下限（字节）": storage.get("min_free_bytes"),
                "可用 inode": storage.get("free_inodes"),
            }
        except (RuntimeUnavailable, ValueError):
            fields = {"本次检查": "无法取得 Live 观察；网络、认证或发布身份可能不匹配。"}
        rows = [
            f"<tr><th>{_text(name)}</th><td>{_text('未知' if value is None else value)}</td></tr>"
            for name, value in fields.items()
        ]
        content = (
            "<section class='panel'><h1>Live 运行诊断</h1>"
            "<p>只读观察，不连接柜台、不修复存储、不改变接收或执行权限。"
            "OK 仅表示数据库可达与来源盘余量充足，不代表可交易、行情新鲜或账户已核对。</p>"
            + _table(["检查项", "观察结果"], rows)
            + "<p><a href='/live'>重新检查</a> · "
            "整机失联告警与数据库磁盘监控尚未接入。</p></section>"
        )
        return workspace_page(access, request, "Live 运行诊断", content, mode="Live · 只读诊断")
