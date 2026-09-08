from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import cast
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse

from northstar_quant.web_access import (
    WorkspaceAccess,
)

_DECISION_LABELS = {
    "BUY": "买入",
    "SELL": "卖出",
    "ALLOW": "通过",
    "REDUCE": "缩减",
    "REJECT": "拒绝",
    "UNKNOWN": "状态不足",
    "CONTRACT_MISMATCH": "合约不匹配",
    "ACCOUNT_NOT_CURRENT": "账户状态未更新至决策时刻",
    "INTENT_EXPIRED": "意图已失效",
    "NONPOSITIVE_EQUITY": "权益不足",
    "NO_PERMITTED_POSITION": "限制不允许持仓",
    "TARGET_UNCHANGED": "目标持仓未变化",
    "NO_SAFE_FILL_PRICE": "无满足限制的成交价格",
    "REVERSAL_REDUCED_TO_FLAT": "反向目标先平仓",
    "RISK_REDUCING_TARGET": "降低已有敞口",
    "CAPPED_TO_LIMIT": "已按限额缩减目标",
    "WITHIN_LIMITS": "目标在限额内",
}


def _rows(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _money(value: object) -> str:
    return format(Decimal(str(value)), ",.2f")


def _percentage(value: object) -> str:
    return f"{Decimal(str(value)) * 100:.2f}%"


def _decision_text(value: object) -> str:
    if value is None:
        return "—"
    return _text(_DECISION_LABELS.get(str(value), str(value)))


def _page(
    title: str,
    content: str,
    *,
    csrf: str | None = None,
    mode: str = "历史研究 · 本机",
    application_name: str = "Northstar",
    navigation: tuple[tuple[str, str], ...] = (),
) -> str:
    csrf_meta = "" if csrf is None else f'<meta name="northstar-csrf" content="{_text(csrf)}">'
    links = "".join(f'<a href="{_text(url)}">{_text(label)}</a>' for label, url in navigation)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{csrf_meta}
<title>{_text(title)} · Northstar</title><link rel="stylesheet" href="/assets/app.css">
<script src="/assets/app.js" defer></script></head><body>
<header class="topbar"><a class="brand" href="/">{_text(application_name)}</a>
<nav aria-label="应用">{links}</nav>
<span class="mode">{_text(mode)}</span></header>
<main>{content}</main><footer>研究、内部 Paper 与 SimNow 柜台证据分别保存。
SimNow 接收与影子目标不授予交易权限；模拟结果不代表实盘表现。</footer>
</body></html>"""


def _field(
    name: str, label: str, value: object = "", *, placeholder: str = "", kind: str = "text"
) -> str:
    return (
        f'<label>{_text(label)}<input name="{_text(name)}" type="{kind}" '
        f'value="{_text(value)}" placeholder="{_text(placeholder)}" required></label>'
    )


def _table(headers: list[str], rows: list[str]) -> str:
    headings = "".join(f"<th>{_text(header)}</th>" for header in headers)
    body = (
        "".join(rows)
        if rows
        else f'<tr><td colspan="{len(headers)}" class="empty">无记录</td></tr>'
    )
    return (
        f'<div class="table-scroll"><table><thead><tr>{headings}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def workspace_page(
    access: WorkspaceAccess,
    request: Request,
    title: str,
    content: str,
    *,
    mode: str = "历史研究 · 本机",
    runtime_id: UUID | None = None,
) -> HTMLResponse:
    identifier = access.open(request)
    csrf = access.require_id(identifier)
    document = _page(
        title,
        content,
        csrf=csrf,
        mode=mode,
        application_name=request.app.title,
        navigation=request.app.state.navigation,
    )
    if runtime_id is not None:
        document = document.replace(
            "</head>", f'<meta name="northstar-live-runtime" content="{runtime_id}"></head>', 1
        )
    response = HTMLResponse(document)
    access.set_cookie(request, response, identifier)
    return response
