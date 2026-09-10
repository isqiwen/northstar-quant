"""Owned browser session policy for HTTP commands and connected UI events."""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections import deque
from ipaddress import ip_address
from threading import Lock

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from northstar_quant.web.passwords import validate_password_hash, verify_password

_LOG = logging.getLogger(__name__)

COOKIE = "northstar_workspace_session"
_SESSION_SECONDS = 1800
_DENIED = "工作台会话缺失或已过期。请重新登录工作台。"


class WorkspaceAccess:
    """Process-local browser identity, never broker credentials or execution authority."""

    def __init__(
        self,
        cookie: str = COOKIE,
        *,
        allowed_hosts: tuple[str, ...] = (),
        allow_ip_hosts: bool = False,
        password_hash: str,
    ) -> None:
        validate_password_hash(password_hash)
        self._password_hash = password_hash
        self._login_lock = Lock()
        self._attempts: deque[float] = deque()
        self.cookie = cookie
        self.allow_ip_hosts = allow_ip_hosts
        self.allowed_hosts = {"127.0.0.1", "localhost", *allowed_hosts}
        self._sessions: dict[str, tuple[str, float]] = {}

    def login(self, request: Request, password: str) -> str:
        # One bounded KDF at a time, outside the trading kernel and asyncio loop.
        if not self._login_lock.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="登录繁忙，请稍后重试。")
        try:
            now = time.monotonic()
            while self._attempts and self._attempts[0] <= now - 60:
                self._attempts.popleft()
            if len(self._attempts) >= 5:
                raise HTTPException(status_code=429, detail="登录尝试过多，请稍后重试。")
            self._attempts.append(now)
            if not verify_password(password, self._password_hash):
                _LOG.info("workspace_login_rejected")
                raise HTTPException(status_code=401, detail="密码不正确。")
            self._sessions = {k: v for k, v in self._sessions.items() if v[1] > now}
            self._sessions.pop(request.cookies.get(self.cookie, ""), None)
            if len(self._sessions) >= 64:
                del self._sessions[next(iter(self._sessions))]
            identifier = secrets.token_urlsafe(32)
            self._sessions[identifier] = (secrets.token_urlsafe(32), now + _SESSION_SECONDS)
            _LOG.info("workspace_login operator=owner")
            return identifier
        finally:
            self._login_lock.release()

    def describe(self, request: Request, identifier: str | None = None) -> dict[str, object]:
        from datetime import UTC, datetime, timedelta

        identifier = identifier or request.cookies.get(self.cookie, "")
        session = self._sessions.get(identifier)
        remaining = 0.0 if session is None else session[1] - time.monotonic()
        if session is None or remaining <= 0:
            return {"authenticated": False, "csrf": None, "operator": None, "expires_at": None}
        return {
            "authenticated": True,
            "csrf": session[0],
            "operator": "owner",
            "expires_at": (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat(),
        }

    def logout(self, request: Request) -> None:
        self.protect(request)
        self._sessions.pop(request.cookies.get(self.cookie, ""), None)
        _LOG.info("workspace_logout operator=owner")

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
            raise HTTPException(status_code=401, detail=_DENIED)
        return session[0]

    def session_id(self, scope: Scope) -> str:
        identifier = HTTPConnection(scope).cookies.get(self.cookie, "")
        self.require_id(identifier)
        scope.setdefault("state", {})["operator"] = "owner"
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
        match = re.fullmatch(
            r"(\[[0-9a-fA-F:]+\]|[a-zA-Z0-9.-]+)(?::([1-9][0-9]{0,4}))?", authority
        )
        host = match[1].strip("[]").lower() if match else ""
        try:
            ip_address(host)
            permitted_ip = self.allow_ip_hosts
        except ValueError:
            permitted_ip = False
        if match is None or (host not in self.allowed_hosts and not permitted_ip):
            raise HTTPException(status_code=403, detail="未允许的工作台地址。")
        if match[2] and int(match[2]) > 65535:
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
                path = scope.get("path", "")
                publication = bool(re.fullmatch(r"/api/publications(?:/[0-9a-f-]{36})?", path))
                if scope["type"] == "websocket" or (
                    path.startswith("/api/")
                    and path not in {"/api/browser-session", "/api/login"}
                    and not publication
                ):
                    self.access.session_id(scope)
                    if scope["type"] == "http" and scope.get("method") not in {"GET", "HEAD"}:
                        self.access.protect(Request(scope))
            except HTTPException as error:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"detail": error.detail}, status_code=error.status_code)(
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
