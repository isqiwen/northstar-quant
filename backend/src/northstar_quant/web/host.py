"""Shared HTTP/security plumbing, with no business composition or background workers."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from northstar_quant.web import security
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, ProtobufRoute


class BrowserSession(ApiModel):
    authenticated: bool
    csrf: str | None
    operator: str | None
    expires_at: str | None


class LoginRequest(ApiModel):
    password: str = Field(min_length=1, max_length=1024, repr=False)


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
    allowed_hosts: tuple[str, ...] = (),
    allow_ip_hosts: bool = False,
) -> FastAPI:
    access = WorkspaceAccess(
        cookie=title.split(" · ")[0].lower().replace(" ", "_") + "_session",
        allowed_hosts=allowed_hosts,
        allow_ip_hosts=allow_ip_hosts,
        password_hash=os.environ.get("NORTHSTAR_WORKSPACE_PASSWORD_HASH", ""),
    )

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
        responses={
            code: {"model": HttpError} for code in (401, 403, 404, 409, 413, 415, 422, 429, 503)
        },
    )
    app.router.route_class = ProtobufRoute
    app.state.workspace_access = access
    app.state.navigation = navigation
    security.install(app, access)

    @app.get("/api/browser-session", response_model=BrowserSession)
    def browser_session(request: Request) -> JSONResponse:
        return JSONResponse(access.describe(request))

    @app.post("/api/login", response_model=BrowserSession)
    def login(body: LoginRequest, request: Request) -> JSONResponse:
        identifier = access.login(request, body.password)
        response = JSONResponse(access.describe(request, identifier))
        access.set_cookie(request, response, identifier)
        return response

    @app.post("/api/logout", response_model=BrowserSession)
    def logout(request: Request) -> JSONResponse:
        access.logout(request)
        response = JSONResponse(access.describe(request))
        response.delete_cookie(access.cookie, httponly=True, samesite="strict")
        return response

    @app.get("/health/ready", response_model=Readiness)
    def ready() -> dict[str, str]:
        # Web readiness only; owning application/kernel diagnostics are separate.
        return {"status": "ready"}

    return app
