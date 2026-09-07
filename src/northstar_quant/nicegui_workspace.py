"""One NiceGUI host with the existing workspace's per-connection authority.

NiceGUI owns its transport and client lifecycle. This Module only binds each
Socket.IO connection to the workspace session which created its page, before
delegating the installed NiceGUI handlers. UI messages never confer broker rights.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from html import escape
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from nicegui import core, ui
from nicegui.client import Client

from northstar_quant.live import LiveClient
from northstar_quant.web_access import WorkspaceAccess

_mounted = False
_configuration_lock = threading.Lock()
_active: WorkspaceAccess | None = None
_connections: dict[str, tuple[Client, WorkspaceAccess, str]] = {}


async def _delegate(handler: Callable[..., Any], *args: Any) -> Any:
    result = handler(*args)
    return await result if inspect.isawaitable(result) else result


def _owner(client: Client, scope: dict[str, Any]) -> tuple[WorkspaceAccess, str]:
    try:
        state = client.request.state
    except RuntimeError:
        # NiceGUI can have request-less script clients; they have no browser
        # session in this workspace and must never acquire a socket identity.
        raise HTTPException(403, "工作台连接没有所属页面。") from None
    access = getattr(state, "workspace_access", None)
    session_id = getattr(state, "workspace_session_id", None)
    if access is not _active or not isinstance(access, WorkspaceAccess):
        raise HTTPException(403, "工作台连接不属于当前应用。")
    access.check_scope(scope, socket=True)
    if not isinstance(session_id, str) or access.session_id(scope) != session_id:
        raise HTTPException(403, "工作台连接不属于此页面。")
    access.require_id(session_id)
    return access, session_id


def _guard_sockets() -> None:
    # `on` is Socket.IO's public registration seam. Keep the selected NiceGUI
    # implementation as the delegate; do not interpret Engine.IO protocol frames.
    handlers = dict(core.sio.handlers["/"])

    async def connect(sid: str, environ: dict[str, Any], auth: Any = None) -> Any:
        try:
            query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=16)
            identifiers = query.get("client_id", [])
            if len(identifiers) != 1:
                return False
            client = Client.instances.get(identifiers[0])
            scope = environ.get("asgi.scope")
            if client is None or not isinstance(scope, dict):
                return False
            access, session_id = _owner(client, scope)
            _connections[sid] = client, access, session_id
            # The native connect handler may invoke its implicit handshake
            # directly, bypassing the separately registered handshake event.
            result = await _delegate(handlers["connect"], sid, environ, auth)
            if result is False:
                _connections.pop(sid, None)
            return result
        except (HTTPException, KeyError, TypeError, ValueError):
            _connections.pop(sid, None)
            return False

    def guarded(name: str) -> Callable[..., Any]:
        async def receive(sid: str, message: Any) -> Any:
            binding = _connections.get(sid)
            if binding is None or not isinstance(message, dict):
                return False
            client, access, session_id = binding
            if access is not _active or message.get("client_id") != client.id:
                return False
            try:
                access.require_id(session_id)
            except HTTPException:
                await core.sio.disconnect(sid)
                return False
            return await _delegate(handlers[name], sid, message)

        return receive

    async def disconnect(sid: str, *_reason: Any) -> None:
        try:
            await _delegate(handlers["disconnect"], sid)
        finally:
            _connections.pop(sid, None)

    core.sio.on("connect", handler=connect)
    for name in ("handshake", "event", "javascript_response", "ack", "log"):
        core.sio.on(name, handler=guarded(name))
    core.sio.on("disconnect", handler=disconnect)


def mount_workspace(
    app: FastAPI,
    access: WorkspaceAccess,
    live: LiveClient,
) -> None:
    """Mount the server workspace once; a server restart requires a new process.

    The parent owns HTTP/ASGI protection and initial session issuance, including
    setting workspace_access and workspace_session_id on the page request state.
    No startup, reconnect or GET invokes a broker command.
    """
    global _mounted
    with _configuration_lock:
        if _mounted:
            raise RuntimeError("NiceGUI workspace is already mounted; restart in a new process")
        _mounted = True
    parent_lifespan = app.router.lifespan_context

    # NiceGUI's annotation narrows this to its APIRouter subclass, but its page
    # Interface only uses the inherited FastAPI router methods and prefix.
    @ui.page("/streams/{stream_id}", api_router=app.router)  # type: ignore[arg-type]
    async def stream_detail(request: Request, stream_id: UUID) -> Response | None:
        from northstar_quant.web import stream

        if getattr(request.state, "workspace_access", None) is not access:
            raise HTTPException(403, "工作台页面未通过授权。")
        session_id = request.state.workspace_session_id
        csrf = access.require_id(session_id)
        ui.add_head_html(f'<meta name="northstar-csrf" content="{escape(csrf, quote=True)}">')

        def authorize() -> None:
            access.require_id(session_id)

        try:
            await stream.show(stream_id, live, authorize=authorize)
        except LookupError:
            return JSONResponse({"detail": "没有找到这份持续接收记录。"}, status_code=404)
        return None

    @asynccontextmanager
    async def lifespan(parent: FastAPI) -> AsyncIterator[Any]:
        global _active
        _active = access
        try:
            async with parent_lifespan(parent) as state:
                yield state
        finally:
            for sid, (_, owner, _) in list(_connections.items()):
                if owner is access:
                    await core.sio.disconnect(sid)
            for client in list(Client.instances.values()):
                try:
                    client_owner = getattr(client.request.state, "workspace_access", None)
                except RuntimeError:
                    continue
                if client_owner is access:
                    client.delete()
            await core.sio.shutdown()
            _active = None

    app.router.lifespan_context = lifespan
    ui.run_with(
        app,
        gzip_middleware_factory=None,
        tailwind=False,
        prod_js=True,
        language="zh-CN",
        show_welcome_message=False,
    )
    _guard_sockets()
