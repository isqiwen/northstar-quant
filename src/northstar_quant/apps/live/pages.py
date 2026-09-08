"""Native NiceGUI Live overview and guarded continuous reception detail."""

from uuid import UUID

from fastapi import FastAPI, Request
from nicegui import ui
from starlette.concurrency import run_in_threadpool

from northstar_quant.apps.live import stream
from northstar_quant.live import LiveClient, RuntimeUnavailable
from northstar_quant.web.components import page_header, record


def register(app: FastAPI, live: LiveClient) -> None:
    @ui.page("/", api_router=app.router)  # type: ignore[arg-type]
    async def overview(request: Request) -> None:
        page_header(request)
        ui.label("Live 运行概览").props("role=heading")
        ui.label("管理端可独立重启；内核离线时不创建替代运行，也不连接柜台。")
        try:
            observation = await run_in_threadpool(live.status)
            record(observation)
        except (RuntimeUnavailable, ValueError):
            ui.label("Live 内核不可用：未确认当前运行状态。").props("color=negative")
        ui.button("刷新观察", on_click=lambda: ui.navigate.reload())

    @ui.page("/streams/{stream_id}", api_router=app.router)  # type: ignore[arg-type]
    async def detail(request: Request, stream_id: UUID) -> None:
        authorize = page_header(request)
        await stream.show(stream_id, live, authorize=authorize)
