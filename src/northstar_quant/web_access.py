"""One local browser session policy for HTTP commands and connected UI events."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from html.parser import HTMLParser

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "northstar_workspace_session"
_LOCAL_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::[1-9][0-9]{0,4})?\Z")
_SESSION_SECONDS = 1800
_DENIED = "工作台会话缺失或已过期。请重新打开工作台页面后操作。"


class WorkspaceAccess:
    """Process-local browser identity, never broker credentials or execution authority."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[str, float]] = {}

    def open(self, request: Request) -> str:
        now = time.monotonic()
        self._sessions = {key: value for key, value in self._sessions.items() if value[1] > now}
        identifier = request.cookies.get(COOKIE, "")
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
            COOKIE,
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
        identifier = HTTPConnection(scope).cookies.get(COOKIE, "")
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
        authority = headers.get("host", "")
        if _LOCAL_HOST.fullmatch(authority) is None:
            raise HTTPException(status_code=403, detail="仅接受本机访问。")
        if ":" in authority and int(authority.rsplit(":", 1)[1]) > 65535:
            raise HTTPException(status_code=403, detail="无效的本机地址。")
        # This app has no reverse proxy. Never let forwarded headers expand trust.
        if any(name == "forwarded" or name.startswith("x-forwarded-") for name in headers):
            raise HTTPException(status_code=403, detail="本机工作台不接受代理转发头。")
        if socket or scope.get("method") not in {"GET", "HEAD"}:
            scheme = "https" if scope.get("scheme") in {"https", "wss"} else "http"
            origin = headers.get("origin")
            if (socket or origin is not None) and origin != f"{scheme}://{authority}":
                raise HTTPException(status_code=403, detail="仅接受同源操作。")
            if headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
                raise HTTPException(status_code=403, detail="仅接受同源操作。")

    def close(self) -> None:
        self._sessions.clear()


class LocalWorkspaceMiddleware:
    """Reject untrusted HTTP and WebSocket origins before either transport is accepted."""

    def __init__(self, app: ASGIApp, access: WorkspaceAccess) -> None:
        self.app = app
        self.access = access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            socket = scope["type"] == "websocket" or "/_nicegui_ws/" in scope["path"]
            try:
                self.access.check_scope(scope, socket=socket)
                if socket:
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


class _InlineScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.hashes: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and "src" not in dict(attrs):
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._parts is not None:
            digest = hashlib.sha256("".join(self._parts).encode()).digest()
            self.hashes.append("'sha256-" + base64.b64encode(digest).decode() + "'")
            self._parts = None


def content_security_policy(html: bytes | None = None) -> str:
    """Only the NiceGUI document needs its fixed runtime and exact bootstrap scripts."""
    script = "'self'"
    style = "'self'"
    images = "'self'"
    extra = ""
    if html is not None:
        parser = _InlineScripts()
        parser.feed(html.decode("utf-8"))
        script += " 'unsafe-eval' " + " ".join(parser.hashes)
        style += " 'unsafe-inline'"
        images += " data:"
        extra = "font-src 'self' data:; "
    return (
        f"default-src 'self'; script-src {script}; style-src {style}; "
        f"connect-src 'self'; img-src {images}; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'; " + extra
    )
