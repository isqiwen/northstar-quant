"""Shared HTTP/security plumbing, with no business composition or background workers."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from northstar_quant.web import security
from northstar_quant.web_access import WorkspaceAccess


def create_host(
    title: str,
    navigation: tuple[tuple[str, str], ...],
    *,
    close: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    access = WorkspaceAccess(cookie=title.split(" · ")[0].lower().replace(" ", "_") + "_session")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            access.close()
            if close is not None:
                await close()

    app = FastAPI(title=title, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.workspace_access = access
    app.state.navigation = navigation
    security.install(app, access)

    @app.get("/api/browser-session")
    def browser_session(request: Request) -> JSONResponse:
        identifier = access.open(request)
        response = JSONResponse({"csrf": access.require_id(identifier)})
        access.set_cookie(request, response, identifier)
        return response

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        # Web readiness only; owning application/kernel diagnostics are separate.
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
