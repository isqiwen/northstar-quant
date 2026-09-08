"""Shared HTTP/security plumbing, with no business composition or background workers."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from northstar_quant.web import security
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, ProtobufRoute


class BrowserSession(ApiModel):
    csrf: str


class Readiness(ApiModel):
    status: Literal["ready"]


class HttpError(ApiModel):
    detail: str
    status: Literal["UNKNOWN"] | None = None
    request_id: str | None = None
    runtime_id: str | None = None
    url: str | None = None
    rejection_id: str | None = None


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

    app = FastAPI(
        title=title,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
        responses={code: {"model": HttpError} for code in (403, 404, 409, 413, 415, 422, 503)},
    )
    app.router.route_class = ProtobufRoute
    app.state.workspace_access = access
    app.state.navigation = navigation
    security.install(app, access)

    @app.get("/api/browser-session", response_model=BrowserSession)
    def browser_session(request: Request) -> JSONResponse:
        identifier = access.open(request)
        response = JSONResponse({"csrf": access.require_id(identifier)})
        access.set_cookie(request, response, identifier)
        return response

    @app.get("/health/ready", response_model=Readiness)
    def ready() -> dict[str, str]:
        # Web readiness only; owning application/kernel diagnostics are separate.
        return {"status": "ready"}

    return app
