"""NiceGUI presentation shared by app-owned pages; no business state or execution."""

import json
from collections.abc import Callable
from html import escape

from fastapi import Request
from nicegui import ui


def page_header(request: Request) -> Callable[[], None]:
    access = request.state.workspace_access
    identifier = request.state.workspace_session_id
    token = access.require_id(identifier)
    ui.add_head_html(f'<meta name="northstar-csrf" content="{escape(token, quote=True)}">')
    with ui.header():
        ui.label(request.app.title)
        for title, path in request.app.state.navigation:
            ui.link(title, path).props("color=white")
    ui.label("研究、内部 Paper、柜台仿真与实盘证据分别解释；页面访问不授予交易权限。")
    return lambda: access.require_id(identifier)


def record(value: object) -> None:
    ui.code(json.dumps(value, ensure_ascii=False, indent=2, default=str), language="json")
