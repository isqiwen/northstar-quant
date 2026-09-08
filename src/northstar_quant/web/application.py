"""Compose Console without owning a broker connection or receiver lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from northstar_quant.data.files import SourceFiles
from northstar_quant.data.library import DataLibrary
from northstar_quant.live import LiveClient
from northstar_quant.runs import RunStore
from northstar_quant.sessions import SessionStore
from northstar_quant.web import broker_routes, data_routes, paper_routes, research_routes, security
from northstar_quant.web_access import WorkspaceAccess

from . import live_health


def application() -> FastAPI:
    from northstar_quant.db import open_database

    engine = open_database()
    return create_workspace(engine, DataLibrary(engine, SourceFiles.from_environment()))


def create_workspace(
    engine: Engine, library: DataLibrary, *, live: LiveClient | None = None
) -> FastAPI:
    """Mount NiceGUI once per Console; all Live calls cross its HTTP Interface."""
    from northstar_quant.nicegui_workspace import mount_workspace

    app = create_app(engine, library, live=live)
    mount_workspace(app, app.state.workspace_access, app.state.live)
    return app


def create_app(engine: Engine, library: DataLibrary, *, live: LiveClient | None = None) -> FastAPI:
    """Data/Research remain local until their own extraction; trading is remote."""
    live = LiveClient.from_environment() if live is None else live
    store, paper, access = RunStore(engine), SessionStore(engine, library), WorkspaceAccess()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            access.close()
            await run_in_threadpool(live.close)

    app = FastAPI(
        title="Northstar · 个人量化工作台", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.workspace_access = access
    app.state.live = live
    security.install(app, access)
    data_routes.register(app, access, library)
    paper_routes.register(app, access, library, paper)
    research_routes.register(app, access, library, store)
    broker_routes.register(app, access, live, paper)
    live_health.register(app, access, live)

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        from northstar_quant.db import require_current_database

        try:
            require_current_database(engine)
        except ValueError as error:
            raise HTTPException(status_code=503, detail="数据库尚未初始化为当前版本。") from error
        store.list(limit=1)
        return {"status": "ready"}

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

    return app
