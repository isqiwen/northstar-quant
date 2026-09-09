"""Owned browser session policy for HTTP commands and connected UI events."""

from __future__ import annotations

import os
import re
import secrets
import time
from ipaddress import IPv4Address

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "northstar_workspace_session"
_SESSION_SECONDS = 1800
_DENIED = "工作台会话缺失或已过期。请重新打开工作台页面后操作。"


def lan_hosts(hostname: str) -> tuple[str, ...]:
    """Deployment supplies actual host IPs; never trust arbitrary browser Host values."""
    addresses = tuple(
        str(IPv4Address(value))
        for value in os.environ.get("NORTHSTAR_WEB_HOSTS", "").split(",")
        if value
    )
    return (hostname, *addresses)


class WorkspaceAccess:
    """Process-local browser identity, never broker credentials or execution authority."""

    def __init__(self, cookie: str = COOKIE, *, allowed_hosts: tuple[str, ...] = ()) -> None:
        self.cookie = cookie
        self.allowed_hosts = {"127.0.0.1", "localhost", *allowed_hosts}
        self._sessions: dict[str, tuple[str, float]] = {}

    def open(self, request: Request) -> str:
        now = time.monotonic()
        self._sessions = {key: value for key, value in self._sessions.items() if value[1] > now}
        identifier = request.cookies.get(self.cookie, "")
        if identifier not in self._sessions:
            if len(self._sessions) >= 64:
                del self._sessions[next(iter(self._sessions))]
            identifier = secrets.token_urlsafe(32)
            self._sessions[identifier] = (secrets.token_urlsafe(32), now + _SESSION_SECONDS)
        request.state.workspace_access = self
        request.state.workspace_session_id = identifier
        return identifier

    def set_cookie(self, request: Request, response: Response, identifier: str) -> None:
        self.require_id(identifier)
        response.set_cookie(
            self.cookie,
            identifier,
            max_age=max(1, int(self._sessions[identifier][1] - time.monotonic())),
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )

    def require_id(self, identifier: str) -> str:
        session = self._sessions.get(identifier)
        if session is None or session[1] <= time.monotonic():
            raise HTTPException(status_code=403, detail=_DENIED)
        return session[0]

    def session_id(self, scope: Scope) -> str:
        identifier = HTTPConnection(scope).cookies.get(self.cookie, "")
        self.require_id(identifier)
        return identifier

    def require_request(self, request: Request) -> str:
        return self.require_id(self.session_id(request.scope))

    def protect(self, request: Request) -> None:
        expected = self.require_request(request)
        supplied = request.headers.get("x-northstar-csrf", "")
        if not supplied.isascii() or not secrets.compare_digest(expected, supplied):
            raise HTTPException(
                status_code=403, detail="工作台操作校验失败。请重新打开页面后操作。"
            )

    def check_scope(self, scope: Scope, *, socket: bool = False) -> None:
        headers = HTTPConnection(scope).headers
        if re.fullmatch(r"/api/publications(?:/[0-9a-f-]{36})?", scope.get("path", "")):
            if socket or scope.get("method") != "GET":
                raise HTTPException(status_code=403, detail="Publication access is read-only")
            return
        authority = headers.get("host", "")
        match = re.fullmatch(r"([a-zA-Z0-9.-]+)(?::([1-9][0-9]{0,4}))?", authority)
        if match is None or match[1].lower() not in self.allowed_hosts:
            raise HTTPException(status_code=403, detail="未允许的工作台地址。")
        if ":" in authority and int(authority.rsplit(":", 1)[1]) > 65535:
            raise HTTPException(status_code=403, detail="无效的工作台地址。")
        # The frontend passes checked Host/Origin directly; forwarded headers grant no trust.
        if any(name == "forwarded" or name.startswith("x-forwarded-") for name in headers):
            raise HTTPException(status_code=403, detail="工作台不接受代理转发头。")
        if socket or scope.get("method") not in {"GET", "HEAD"}:
            scheme = "https" if scope.get("scheme") in {"https", "wss"} else "http"
            origin = headers.get("origin")
            if (socket or origin is not None) and origin != f"{scheme}://{authority}":
                raise HTTPException(status_code=403, detail="仅接受同源操作。")
            if headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
                raise HTTPException(status_code=403, detail="仅接受同源操作。")

    def close(self) -> None:
        self._sessions.clear()


class WorkspaceMiddleware:
    """Reject untrusted HTTP and WebSocket origins before either transport is accepted."""

    def __init__(self, app: ASGIApp, access: WorkspaceAccess) -> None:
        self.app = app
        self.access = access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            try:
                self.access.check_scope(scope, socket=scope["type"] == "websocket")
                if scope["type"] == "websocket":
                    self.access.session_id(scope)
            except HTTPException as error:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"detail": error.detail}, status_code=403)(
                        scope, receive, send
                    )
                return
        await self.app(scope, receive, send)


def content_security_policy() -> str:
    """External bundled scripts only; component styling never enables script evaluation."""
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
