"""Render saved broker facts; opening a page never connects to the counter."""

from __future__ import annotations

import json
from html import escape
from typing import cast

_STATUS = {
    "PENDING": "未完成／进程可能已中断",
    "FAILED": "查询失败",
    "INCOMPLETE": "回包不完整",
    "COMPLETE": "回包齐全（未对账）",
}
_SECTIONS = {
    "account": "资金账户",
    "positions": "全账户持仓（含今昨仓字段）",
    "orders": "全账户委托",
    "trades": "全账户成交",
    "instrument": "指定期货合约",
    "margin": "指定合约保证金率",
    "commission": "指定合约手续费率",
}


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _json(value: object) -> str:
    return "<pre>" + _text(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"


def workspace(status: dict[str, object], batches: list[dict[str, object]]) -> str:
    credentials, sdk = _object(status["credentials"]), _object(status["sdk"])
    options = "".join(
        f'<option value="{_text(profile["name"])}">{_text(profile["name"])}</option>'
        for profile in cast(list[dict[str, object]], status["profiles"])
    )
    disabled = "" if credentials["configured"] and sdk["available"] else " disabled"
    rows = "".join(
        f'<tr><td><a href="/broker/{_text(batch["batch_id"])}">{_text(batch["created_at"])}</a>'
        f"</td><td>{_text(_object(batch['profile'])['name'])}</td>"
        f"<td>{_text(batch['account_id'])}</td><td>{_text(batch['instrument'])}</td>"
        f"<td>{_text(_STATUS[str(batch['status'])])}</td></tr>"
        for batch in batches
    )
    if not rows:
        rows = '<tr><td colspan="5">尚无柜台查询记录；没有连接过不等于空账户。</td></tr>'
    return f"""<section class="intro"><p class="eyebrow">SIMNOW · 外部模拟柜台</p>
<h1>连接与账户事实</h1><p>先确认连接、身份和查询范围，再推进受限模拟交易。</p></section>
<aside class="data-notice"><strong>当前阶段：显式只读查询</strong>
<p>不会下单、撤单、确认结算或转账。后续模拟执行属于下一阶段；
当前结果既不是内部 Paper，也不是已对账的实盘账户。</p></aside>
<div class="workspace"><section class="panel"><h2>本机准备情况</h2>
<p>私密配置：{"可读取，尚不代表登录成功" if credentials["configured"] else "待配置"}</p>
<p class="muted">{_text(credentials["reason"])}</p>
<p>CTP SDK：{"已安装，尚不代表连接成功" if sdk["available"] else "当前不可用"}</p>
<p class="muted">{_text(sdk["reason"] or sdk["binding_version"])}</p>
<p>在自己的终端运行 <code>bash scripts/setup_simnow.sh</code> 保存凭据，
通过 <code>compose.simnow.yaml</code> 挂载给同一个应用。网页不接收密码。</p>
<details><summary>已批准的两个模拟环境</summary>{_json(status["profiles"])}</details>
</section><section class="panel"><h2>发起一次查询</h2>
<form id="broker-query-form"><label>模拟环境
<select name="profile" required>{options}</select></label>
<label>一个具体期货合约<input name="instrument" required placeholder="例如 rb2610"
pattern="[A-Za-z]{{1,3}}[0-9]{{3,4}}"></label>
<p class="muted">资金、持仓、委托、成交查询整个账户；合约与费率只查询指定合约。
保存本次有限查询的回包、失败与缺项，不会自动重连。</p>
<button type="submit"{disabled}>连接并查询（只读）</button>
<p id="broker-query-status" role="status"></p></form></section></div>
<section class="panel"><h2>已保存的查询</h2><div class="table-scroll"><table>
<thead><tr><th>发起时间（UTC）</th><th>环境</th><th>账户</th><th>指定合约</th><th>结果</th>
</tr></thead><tbody>{rows}</tbody></table></div></section>"""


def report(batch: dict[str, object]) -> str:
    completeness = _object(batch["completeness"])
    sections = _object(completeness["sections"])
    panels = []
    for name, value in sections.items():
        section = _object(value)
        rows = section["rows"]
        note = (
            "本项已收到完整响应，结果为空。"
            if rows == [] and section["status"] == "COMPLETE"
            else "尚无完整响应，不能解释为空。"
            if section["status"] != "COMPLETE"
            else "以下为柜台返回字段，未补填或推算账户事实。"
        )
        panels.append(
            f'<section class="panel"><h2>{_text(_SECTIONS.get(name, name))}</h2>'
            f"<p>{_text(section['status'])} · {_text(note)}</p>"
            f'<p class="muted">首个回包 {_text(section["first_received_at"])} / '
            f"末个回包 {_text(section['last_received_at'])}</p>{_json(rows)}</section>"
        )
    capture = batch["capture"]
    capture_info = (
        None
        if capture is None
        else {key: value for key, value in _object(capture).items() if key != "events"}
    )
    return f"""<section class="intro"><p class="eyebrow">SIMNOW · 固定查询记录</p>
<h1>{_text(_STATUS[str(batch["status"])])}</h1>
<p>{_text(_object(batch["profile"])["name"])} · 账户 {_text(batch["account_id"])} ·
{_text(batch["instrument"])}</p><a href="/broker">返回连接工作台</a></section>
<aside class="data-notice"><strong>未对账 · 不具备执行权限</strong>
<p>各查询分时返回，不构成同一时刻的原子账户快照。本地柜台账本尚未建立，
差异未知，不会把未知写成零，也不会覆盖研究或 Paper 账本。</p></aside>
<section class="panel"><h2>查询范围与证据</h2><p>命令身份 {_text(batch["batch_id"])}</p>
<p>身份 {_text(completeness["identity"])} · 柜台交易日 {_text(completeness["trading_day"])}</p>
{_json(capture_info)}<details><summary>失败、缺项与未完成对账的原因</summary>
{_json(batch["reconciliation"])}</details>
<details><summary>环境、查询范围与实现身份</summary>
{_json({key: batch[key] for key in ("profile", "query_scope", "implementation_hash")})}</details>
<a href="/api/broker/queries/{_text(batch["batch_id"])}">查看完整固定回包记录（本机私有）</a>
</section><div class="workspace">{"".join(panels)}</div>
<section class="panel"><h2>行情观察</h2><p>仅本次有限连接的订阅与行情证据，
不是持续行情服务；无行情不代表价格为零。</p>{_json(batch["market"])}</section>"""
