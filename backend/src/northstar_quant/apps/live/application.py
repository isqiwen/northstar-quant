"""Live management uses only the kernel Interface, not a database or broker SDK."""

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from northstar_quant.apps.logging import logged_application
from northstar_quant.web.host import create_host
from northstar_quant.web.protobuf import bind

from . import archive_api, broker_api, diagnostics_api, order_api, stream_api
from .instances import Instances


def create_app(*, instances: Instances) -> FastAPI:
    async def close() -> None:
        await run_in_threadpool(instances.close)

    app = create_host(
        "Northstar Live · 实盘交易系统",
        (
            ("运行概览", "/"),
            ("连接与查询", "/broker"),
            ("持续行情", "/streams"),
            ("运行诊断", "/live"),
        ),
        close=close,
        allowed_hosts=("live.wangqiwen.me",),
        allow_ip_hosts=True,
    )
    app.state.instances = instances
    instances.register(app, app.state.workspace_access)
    broker_api.register(app, app.state.workspace_access, instances)
    stream_api.register(app, app.state.workspace_access, instances)
    diagnostics_api.register(app, app.state.workspace_access, instances)
    order_api.register(app, app.state.workspace_access, instances)
    archive_api.register(app, app.state.workspace_access, instances)
    bind(app, "live")
    return app


@logged_application("live", "api")
def application() -> FastAPI:
    return create_app(instances=Instances.from_environment())
