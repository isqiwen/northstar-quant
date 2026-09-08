"""Application lifecycle and safe HTTP diagnostics, independent of domain behavior."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from northstar_quant.logs import LogRuntime

_LOG = logging.getLogger(__name__)


class RequestLog:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 500

        async def observe(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, observe)
        except Exception:
            # Exception values, request payloads, URLs, credentials and query
            # strings are deliberately absent. The sink retains type/frame locations.
            _LOG.exception("unhandled HTTP operation")
            raise
        finally:
            if status >= 400 or scope["method"] != "GET":
                route = getattr(scope.get("route"), "path", "<unmatched>")
                _LOG.log(
                    logging.WARNING if status >= 400 else logging.INFO,
                    "HTTP %s %s status=%d",
                    scope["method"],
                    route,
                    status,
                )


def attach(app: FastAPI, runtime: LogRuntime) -> FastAPI:
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        _LOG.info("application starting")
        try:
            async with original(application):
                _LOG.info("application ready")
                yield
        except Exception:
            _LOG.exception("application lifecycle failed")
            raise
        finally:
            _LOG.info("application stopped")
        # Process-owned logs stay open through Uvicorn's final shutdown messages;
        # bounded atexit drains them after the server has returned.

    app.router.lifespan_context = lifespan
    app.add_middleware(RequestLog)

    @app.get("/health/logging", include_in_schema=False)
    def logging_status() -> dict[str, object]:
        return runtime.status()

    return app
