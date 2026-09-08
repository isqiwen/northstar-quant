from __future__ import annotations

import json
from decimal import Decimal
from html import escape
from typing import cast

from northstar_quant.web.data_views import (
    _availability_notice,
    _data_evidence,
)
from northstar_quant.web.html import (
    _decision_text,
    _money,
    _percentage,
    _rows,
    _table,
    _text,
)
from northstar_quant.web.requests import _object


def _report(run: dict[str, object]) -> str:
    result = _object(run["result"])
    configuration = _object(run["config"])
    data = None if result["data"] is None else _object(result["data"])
    data_notice = "" if data is None else _availability_notice(data)
    data_evidence = "" if data is None else _data_evidence(data)
    market, summary = _object(result["market"]), _object(result["summary"])
    curve, fills, decisions = (
        _rows(result["equity_curve"]),
        _rows(result["fills"]),
        _rows(result["decisions"]),
    )
    currency = _text(market["currency"])
    metrics = "".join(
        f'<div class="metric"><span>{label}</span><strong>{value}</strong>'
        f"<small>{detail}</small></div>"
        for label, value, detail in (
            ("期末权益", _money(summary["ending_equity"]), currency),
            ("净收益率", _percentage(summary["total_return"]), "已扣成交费用与滑点"),
            (
                "最大回撤",
                _percentage(summary["max_drawdown_fraction"]),
                f"{_money(summary['max_drawdown'])} {currency}",
            ),
            ("成交费用", _money(summary["total_fees"]), f"{summary['fill_count']} 次成交"),
        )
    )
    fill_rows = [
        "<tr>"
        + "".join(
            f"<td>{_decision_text(fill[key])}</td>"
            for key in (
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
        for fill in fills[-200:]
    ]
    decision_rows = [
        "<tr>"
        + "".join(
            f"<td>{_decision_cell(key, decision[key])}</td>"
            for key in (
                "at",
                "momentum",
                "target_fraction",
                "outcome",
                "reason",
                "approved_position_lots",
            )
        )
        + "</tr>"
        for decision in decisions[-200:]
    ]
    assumptions = "".join(
        f"<li>{_text(item)}</li>" for item in cast(list[str], result["assumptions"])
    )
    config = escape(json.dumps(run["config"], ensure_ascii=False, sort_keys=True, indent=2))
    pending = (
        "无待成交授权。"
        if result["pending_order"] is None
        else "期末仍有未成交授权，未计为成交。完整信息见下载结果。"
    )
    fills_table = _table(
        ["成交时间（UTC）", "方向", "手数", "成交价", "费用", "已实现损益", "成交后持仓"], fill_rows
    )
    decisions_table = _table(
        ["决策时间（UTC）", "动量", "目标比例", "结果", "原因", "批准持仓"], decision_rows
    )
    return f"""<section class="intro report-intro"><a class="back" href="/">← 返回工作台</a>
<p class="eyebrow">RESEARCH REPORT</p><h1>{_text(market["symbol"])} · 研究结果</h1>
<p>{_text(summary["bar_count"])} 个 bars · {_text(summary["decision_count"])} 次决策 ·
{_text(run["created_at"])}</p>
<div class="actions"><button data-rerun="{_text(run["run_id"])}">重跑相同数据与配置</button>
<a class="button secondary" href="/api/runs/{_text(run["run_id"])}"
download="research-{_text(run["run_id"])}.json">下载完整结果</a></div>
<p id="research-status" role="status" class="status"></p></section>
{data_notice}
<section class="metrics">{metrics}</section>
<section class="panel"><div class="section-title"><h2>权益变化</h2>
<span class="muted">{currency} · 按观测顺序</span></div>
{_equity_chart(curve)}<div class="account-line">
<span>已实现损益 {_money(summary["realized_pnl"])}</span>
<span>未实现损益 {_money(summary["unrealized_pnl"])}</span>
<span>期末持仓 {_text(summary["ending_position_lots"])} 手</span></div>
<p class="muted">{pending} 期末持仓按最后可见价格估值，未虚构平仓。</p></section>
<section class="panel"><div class="section-title"><h2>成交与持仓</h2>
<span class="muted">最近 200 条 / 共 {len(fills)} 条</span></div>{fills_table}</section>
<section class="panel"><div class="section-title"><h2>策略与风险决定</h2>
<span class="muted">最近 200 条 / 共 {len(decisions)} 条</span></div>{decisions_table}</section>
{data_evidence}
<section class="panel"><h2>模型范围</h2><p>这是单合约、单交易日的历史模拟；
成交使用完成的 bar 价格。样本内结果不能证明策略可盈利。</p>
<p>本次模拟按每手每次成交收取 {_money(configuration["fee_per_lot"])} {currency}，
每次成交施加 {_text(configuration["slippage_ticks"])} ticks 的不利滑点，
初始保证金比例为 {_percentage(configuration["initial_margin_fraction"])}。
这些是研究配置中的模拟假设，并非已核实的交易所或期货公司实际条款。</p>
<ul>{assumptions}</ul></section><section class="panel"><details>
<summary>完整配置与可复核身份</summary><pre>{config}</pre><dl class="identity">
<dt>数据快照</dt><dd>{_text(_object(run["snapshot"])["id"])}</dd>
<dt>数据摘要</dt><dd>{_text(_object(run["snapshot"])["content_hash"])}</dd>
<dt>实现与依赖摘要</dt><dd>{_text(run["implementation_hash"])}</dd>
<dt>计算结果摘要</dt><dd>{_text(result["result_hash"])}</dd><dt>研究记录</dt><dd>{_text(run["run_id"])}</dd></dl>
</details></section>"""


def _equity_chart(curve: list[dict[str, object]]) -> str:
    if not curve:
        return '<p class="empty">没有可绘制的观测。</p>'
    values = [Decimal(str(point["equity"])) for point in curve]
    low, high = min(values), max(values)
    span = high - low
    selected = {0, len(values) - 1}
    # Preserve each bucket's extremes instead of hiding drawdowns by taking
    # every nth sample. The full unabridged series remains in the stored result.
    bucket = max(1, (len(values) + 399) // 400)
    for start in range(0, len(values), bucket):
        indexes = range(start, min(start + bucket, len(values)))
        selected.add(min(indexes, key=values.__getitem__))
        selected.add(max(indexes, key=values.__getitem__))
    coordinates = []
    for index in sorted(selected):
        x = Decimal(32) + Decimal(index) * 936 / max(1, len(values) - 1)
        y = Decimal(120) if span == 0 else Decimal(220) - (values[index] - low) * 196 / span
        coordinates.append(f"{x:.2f},{y:.2f}")
    return (
        '<svg class="equity-chart" viewBox="0 0 1000 260" role="img" aria-label="扣费后的权益曲线">'
        '<line x1="32" y1="220" x2="968" y2="220" class="grid-line"/>'
        f'<polyline points="{" ".join(coordinates)}" class="equity-line"/>'
        f'<text x="32" y="16">{_money(high)}</text>'
        f'<text x="32" y="236">{_money(low)}</text>'
        f'<text x="32" y="254">{_text(curve[0]["at"])}</text>'
        f'<text x="968" y="254" text-anchor="end">{_text(curve[-1]["at"])}</text></svg>'
    )


def _decision_cell(name: str, value: object) -> str:
    if name == "momentum" and value is not None:
        return f"{Decimal(str(value)) * 100:.4f}%"
    if name == "target_fraction":
        return _percentage(value)
    return _decision_text(value)
