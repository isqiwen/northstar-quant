from __future__ import annotations

import json
from typing import cast

from northstar_quant.apps.research.views import _decision_cell, _equity_chart
from northstar_quant.research import ResearchConfig
from northstar_quant.web.data_views import _availability_notice, _dataset_label
from northstar_quant.web.html import _field, _money, _rows, _table, _text
from northstar_quant.web.requests import _object


def _configuration_fields() -> str:
    defaults = ResearchConfig().to_dict()
    groups = (
        (
            "策略 · 当前动量实现",
            (
                ("lookback", "动量回看 bars"),
                ("threshold", "动量阈值（小数）"),
                ("target_fraction", "目标仓位比例"),
            ),
        ),
        (
            "Risk · 目标与授权限制",
            (
                ("max_lots", "最大持仓手数"),
                ("max_gross_notional", "最大名义敞口"),
                ("max_margin_fraction", "保证金 / 权益上限"),
                ("max_adverse_price_move_fraction", "授权不利价格变动上限"),
                ("order_lifetime_seconds", "授权有效时间（秒）"),
            ),
        ),
        (
            "内部模拟账户与成交假设",
            (
                ("initial_cash", "独立模拟账户初始资金"),
                ("initial_margin_fraction", "初始保证金比例"),
                ("fee_per_lot", "每手每次成交费用"),
                ("slippage_ticks", "每次成交滑点 ticks"),
            ),
        ),
    )
    return "".join(
        f'<fieldset><legend>{title}</legend><div class="fields">'
        + "".join(_field(name, label, defaults[name]) for name, label in fields)
        + "</div></fieldset>"
        for title, fields in groups
    )


def _paper_workspace(
    configurations: list[dict[str, object]],
    datasets: list[dict[str, object]],
    sessions: list[dict[str, object]],
) -> str:
    configuration_options = "".join(
        f'<option value="{_text(item["configuration_id"])}">'
        f"{_text(item['name'])} · {_text(item['created_at'])} · "
        f"{_text(str(item['configuration_id'])[:12])}</option>"
        for item in configurations
    )
    dataset_options = "".join(
        f'<option value="{_text(item["snapshot_id"])}">{_text(_dataset_label(item))}</option>'
        for item in datasets
    )
    session_rows = []
    for session in sessions:
        summary = _object(session["summary"])
        market = _object(session["market"])
        configuration = _object(session["configuration"])
        session_rows.append(
            f'<tr><td><a href="/paper/{_text(session["session_id"])}">'
            f"{_text(market['symbol'])} · {_text(session['created_at'])}</a></td>"
            f"<td>{_text(configuration['name'])}</td>"
            f"<td>{_paper_status(session)}</td><td>文件输入 · 内部 Paper</td>"
            f"<td>{_text(session['cursor'])} / {_text(session['total_inputs'])}</td>"
            f"<td>{_money(summary['ending_equity'])} {_text(market['currency'])}</td>"
            f"<td>{_text(summary['ending_position_lots'])} 手</td></tr>"
        )
    sessions_table = (
        _table(["会话", "固定配置", "状态", "输入类型", "已处理", "权益", "持仓"], session_rows)
        if session_rows
        else '<p class="empty">还没有 Paper 会话。保存配置后，选择已接受的数据创建。</p>'
    )
    return f"""<section class="intro"><p class="eyebrow">FILE PAPER WORKSPACE</p>
<h1>一条输入，一次可核对的推进。</h1>
<p>保存不可变配置，用已接受的数据创建独立内部 Paper 会话；刷新或重启后仍可查看进度。</p></section>
<aside class="data-notice"><strong>文件输入演示 · 不是持续行情，也不是柜台仿真或实盘</strong>
<p>只在明确点击时处理下一条已保存行情。没有后台自动推进，不连接行情来源或交易账户。
每个新会话使用独立的模拟账户，不能用它替换、重置或控制其他账户。</p></aside>
<div class="workspace"><section class="panel"><div class="section-title">
<span class="step">01</span><h2>保存策略与 Risk 配置</h2></div>
<p class="muted">修改后保存新修订；不会改变已经创建的会话，也不会授予任何实盘执行权限。</p>
<form id="configuration-form">{_field("name", "配置名称", placeholder="例如：RB 单时段动量")}
{_configuration_fields()}
<p class="muted">资金、保证金、费用与滑点均为内部模拟假设，不代表已核实的柜台条款。</p>
<button type="submit">保存不可变配置</button>
<p id="configuration-status" class="status" role="status"></p></form></section>
<section class="panel"><div class="section-title"><span class="step">02</span>
<h2>创建独立 Paper 会话</h2></div>
<form id="paper-create-form"><label>固定配置修订<select name="configuration_id" required>
<option value="">请选择已保存的配置</option>{configuration_options}</select></label>
<div id="configuration-preview" class="configuration-preview" aria-live="polite"></div>
<label>已接受的文件数据<select name="snapshot_id" required>
<option value="">请选择已接受的数据</option>{dataset_options}</select></label>
<div id="paper-data-notice"></div>
<p class="muted">没有数据？<a href="/">先导入并通过质量检查</a>。创建时固定本次数据、
配置与实现身份，不绑定可变的“最新版”。会话从暂停状态开始，不热换配置。</p>
<button type="submit">创建独立会话（默认暂停）</button>
<p id="paper-create-status" class="status" role="status"></p></form></section></div>
<section class="panel"><div class="section-title"><h2>内部 Paper 会话</h2>
<span class="muted">各自独立账户 · 持久保存进度</span></div>{sessions_table}</section>"""


def _paper_status(session: dict[str, object]) -> str:
    return "输入已完成" if session["status"] == "COMPLETED" else "暂停 · 等待明确推进"


def _paper_report(session: dict[str, object]) -> str:
    configuration = _object(session["configuration"])
    summary = _object(session["summary"])
    market = _object(session["market"])
    snapshot = _object(session["snapshot"])
    data_notice = _availability_notice(_object(session["data"]))
    currency = _text(market["currency"])
    config_text = _text(json.dumps(configuration["config"], ensure_ascii=False, indent=2))
    pending = session["pending_order"]
    pending_text = (
        '<p class="muted">无待成交授权。</p>'
        if pending is None
        else "<p>已授权但尚未成交；下一条输入仍须满足有效期和价格限制，不能视为已成交。</p>"
        f"<pre>{_text(json.dumps(pending, ensure_ascii=False, indent=2))}</pre>"
    )
    limitations = "".join(
        f"<li>{_text(item)}</li>" for item in cast(list[str], session["limitations"])
    )
    blocked = session["blocked_reason"]
    blocked_notice = (
        f'<p class="status error">无法推进：{_text(blocked)}</p>' if blocked is not None else ""
    )
    disabled = "" if session["can_advance"] else " disabled"
    metrics = "".join(
        f'<div class="metric"><span>{label}</span><strong>{value}</strong>'
        f"<small>{note}</small></div>"
        for label, value, note in (
            ("模拟账户权益", _money(summary["ending_equity"]), currency),
            ("累计模拟费用", _money(summary["total_fees"]), currency),
            ("当前持仓", _text(summary["ending_position_lots"]), "手 · 不虚构期末平仓"),
            (
                "已处理文件输入",
                f"{_text(session['cursor'])} / {_text(session['total_inputs'])}",
                f"剩余 {_text(session['remaining_inputs'])} 条",
            ),
        )
    )
    last_event = session["last_event"]
    last_text = (
        "尚未处理任何输入；账户为初始资金，策略尚未预热。"
        if last_event is None
        else f"最后已提交观测：{_object(last_event)['at']}"
    )
    fill_rows = [
        "<tr>"
        + "".join(
            f"<td>{_text(fill[name])}</td>"
            for name in (
                "filled_at",
                "side",
                "quantity_lots",
                "price",
                "fee",
                "realized_pnl",
                "position_lots",
            )
        )
        + "</tr>"
        for fill in _rows(session["fills"])[-200:]
    ]
    decision_rows = [
        "<tr>"
        + "".join(
            f"<td>{_decision_cell(name, decision[name])}</td>"
            for name in (
                "at",
                "momentum",
                "target_fraction",
                "outcome",
                "reason",
                "approved_position_lots",
            )
        )
        + "</tr>"
        for decision in _rows(session["decisions"])[-200:]
    ]
    fills_table = _table(
        ["成交时间（UTC）", "方向", "手数", "成交价", "费用", "已实现损益", "成交后持仓"], fill_rows
    )
    decisions_table = _table(
        ["决策时间（UTC）", "动量", "目标比例", "结果", "原因", "批准持仓"], decision_rows
    )
    identities = (
        ("输入类型", session["input_type"]),
        ("固定配置名称", configuration["name"]),
        ("固定配置身份", configuration["configuration_id"]),
        ("策略配置摘要", configuration["strategy_hash"]),
        ("Risk 配置摘要", configuration["risk_hash"]),
        ("实现与依赖摘要", session["implementation_hash"]),
        ("数据快照", snapshot["id"]),
        ("数据摘要", snapshot["content_hash"]),
        ("独立模拟账户", session["account_id"]),
        ("会话身份", session["session_id"]),
        ("最后提交时间", session["updated_at"]),
    )
    identity_text = "".join(
        f"<dt>{label}</dt><dd>{_text(value)}</dd>" for label, value in identities
    )
    return f"""<section class="intro report-intro">
<a class="back" href="/paper">← 返回 Paper 工作台</a>
<p class="eyebrow">INTERNAL PAPER · FILE REPLAY</p><h1>{_text(market["symbol"])} · 文件 Paper</h1>
<p>{_paper_status(session)} · 固定配置：{_text(configuration["name"])} ·
{_text(session["created_at"])}</p>
<div class="actions"><button id="paper-advance"
data-session-id="{_text(session["session_id"])}"{disabled}>核对并推进下一条</button>
<a class="button secondary" href="/api/paper/{_text(session["session_id"])}"
download="paper-{_text(session["session_id"])}.json">下载会话证据</a>
<a class="button secondary" href="/datasets/{_text(snapshot["id"])}">查看来源与质量</a></div>
<p id="paper-advance-status" class="status" role="status"></p>{blocked_notice}</section>
<aside class="data-notice"><strong>内部模拟账户 · 已接受文件逐条回放</strong>
<p>不连接持续行情或柜台，不会发送真实委托。每次明确操作最多推进一条，提交后仍暂停。
“输入已完成”仅表示文件耗尽，不表示已平仓；残余持仓和待单继续可见。</p><ul>{limitations}</ul></aside>
{data_notice}
<section class="metrics">{metrics}</section>
<section class="panel"><div class="section-title"><h2>持久进度与权益</h2>
<span class="muted">{_text(last_text)}</span></div>{_equity_chart(_rows(session["equity_curve"]))}
<div class="account-line"><span>初始模拟资金 {_money(summary["initial_cash"])} {currency}</span>
<span>当前账本现金 {_money(summary["ending_cash"])} {currency}</span>
<span>已实现损益 {_money(summary["realized_pnl"])}</span>
<span>未实现损益 {_money(summary["unrealized_pnl"])}</span></div></section>
<section class="panel"><h2>待成交授权</h2>{pending_text}</section>
<section class="panel"><h2>模拟成交与持仓</h2>{fills_table}</section>
<section class="panel"><h2>策略与风险决定</h2>{decisions_table}</section>
<section class="panel"><h2>实际生效的固定绑定</h2><p class="muted">此会话不可更换配置。
编辑、另存配置不会改变这个账户的资金、持仓、风险记录或进度。新建会话是另一个独立模拟账户。</p>
<details><summary>查看完整配置与身份</summary><pre>{config_text}</pre>
<dl class="identity">{identity_text}</dl></details></section>"""
