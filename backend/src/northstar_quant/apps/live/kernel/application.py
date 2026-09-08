"""One independent authenticated Live process; starting it never connects a broker."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from northstar_quant.apps.storage import open_database, require_current_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live import diagnostics
from northstar_quant.live.auth import LiveAuth
from northstar_quant.live.client import PROTOCOL_VERSION
from northstar_quant.live.owner import LiveOwner

from . import broker_routes, budget_routes, material_routes, stream_routes


class _BoundedCommandBody:
    """Limit streamed command bytes before FastAPI materializes or parses the body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        size = 0
        limit = 4_000_000 if scope.get("path") == "/strategy-materials" else 16_384

        async def bounded_receive() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    raise HTTPException(413, "Live command exceeds its bounded input size")
            return message

        await self.app(scope, bounded_receive, send)


def create_app(engine: Engine, library: DataLibrary, auth: LiveAuth) -> FastAPI:
    if auth.control_token is None:
        raise ValueError("Live requires separate read and control credentials")
    owner = LiveOwner(engine, library)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await run_in_threadpool(owner.close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_BoundedCommandBody)
    app.state.owner = owner
    app.state.auth = auth

    @app.middleware("http")
    async def access(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path != "/health/ready":
            try:
                auth.authorize(
                    request.headers.get("authorization", ""), control=request.method != "GET"
                )
                if request.headers.get("x-northstar-protocol") != PROTOCOL_VERSION:
                    raise HTTPException(409, "Live and Live Web must use the same current protocol")
                if request.method not in {"GET", "POST"}:
                    raise HTTPException(405, "Live operation is not supported")
            except PermissionError as error:
                return JSONResponse({"detail": str(error)}, status_code=403)
            except HTTPException as error:
                return JSONResponse({"detail": error.detail}, status_code=error.status_code)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Northstar-Protocol"] = PROTOCOL_VERSION
        response.headers["X-Live-Runtime-ID"] = str(owner.identifier)
        response.headers["X-Live-Observed-At"] = owner.status()["observed_at"]
        return response

    @app.exception_handler(LookupError)
    async def missing(_: Request, __: LookupError) -> JSONResponse:
        return JSONResponse({"detail": "Live record was not found"}, status_code=404)

    @app.exception_handler(SQLAlchemyError)
    async def storage_unavailable(_: Request, __: SQLAlchemyError) -> JSONResponse:
        return JSONResponse({"detail": "Live storage is unavailable"}, status_code=503)

    @app.exception_handler(ValueError)
    async def invalid(_: Request, __: ValueError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Live rejected the requested domain operation"}, status_code=409
        )

    @app.exception_handler(RequestValidationError)
    async def malformed(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "Live request fields are invalid"}, status_code=422)

    @app.get("/health/ready")
    def ready() -> dict[str, bool]:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ready": True}

    @app.get("/runtime")
    def runtime() -> dict[str, Any]:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return owner.status()

    @app.get("/commands/{command_id}")
    def command(command_id: UUID) -> dict[str, Any]:
        return owner.commands.get(command_id)

    @app.get("/diagnostics")
    def diagnostic_observation() -> dict[str, Any]:
        return owner.read(diagnostics.observe(engine, library))

    app.include_router(material_routes.routes(owner, engine, library))
    app.include_router(stream_routes.routes(owner))
    app.include_router(broker_routes.routes(owner))
    app.include_router(budget_routes.routes(owner))
    return app


def application() -> FastAPI:
    auth = LiveAuth.from_environment(require_control=True)
    engine = open_database()
    require_current_database(engine)
    return create_app(engine, DataLibrary(engine, SourceFiles.from_environment()), auth)
