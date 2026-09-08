"""Live management uses only the kernel Interface, not a database or broker SDK."""

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from northstar_quant.live import LiveClient
from northstar_quant.web.host import create_host

from . import evidence, health, routes, stream_routes


def create_app(*, live: LiveClient) -> FastAPI:
    async def close() -> None:
        await run_in_threadpool(live.close)

    app = create_host(
        "Northstar Live · 实盘交易系统",
        (
            ("运行概览", "/"),
            ("连接与查询", "/broker"),
            ("持续行情", "/streams"),
            ("运行诊断", "/live"),
        ),
        close=close,
    )
    app.state.live = live
    routes.register(app, app.state.workspace_access, live)
    stream_routes.register(app, app.state.workspace_access, live)
    health.register(app, app.state.workspace_access, live)
    evidence.register(app, app.state.workspace_access, live)
    return app


def application() -> FastAPI:
    from northstar_quant.nicegui_workspace import mount_workspace

    from .pages import register

    live = LiveClient.from_environment()
    app = create_app(live=live)
    mount_workspace(app, app.state.workspace_access, lambda: register(app, live))
    return app
