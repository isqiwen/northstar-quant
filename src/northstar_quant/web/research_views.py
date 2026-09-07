from __future__ import annotations

import json
from decimal import Decimal
from html import escape
from typing import cast

from northstar_quant.research import ResearchConfig
from northstar_quant.web.data_views import (
    _CSV_COLUMNS,
    _availability_notice,
    _data_evidence,
    _dataset_label,
    _dataset_list,
    _spec_fields,
)
from northstar_quant.web.html import (
    _decision_text,
    _field,
    _money,
    _percentage,
    _rows,
    _table,
    _text,
)
from northstar_quant.web.requests import _object


def _workspace(
    runs: list[dict[str, object]],
    datasets: list[dict[str, object]],
    selected: dict[str, object] | None,
) -> str:
    defaults = ResearchConfig().to_dict()
    basic_fields = "".join(
        _field(name, label, defaults[name])
        for name, label in (
            ("initial_cash", "初始资金"),
            ("lookback", "动量回看 bars"),
            ("threshold", "动量阈值（小数）"),
            ("target_fraction", "目标仓位比例"),
            ("max_lots", "最大持仓手数"),
            ("fee_per_lot", "每手每次成交费用"),
            ("slippage_ticks", "每次成交滑点 ticks"),
        )
    )
    risk_fields = "".join(
        _field(name, label, defaults[name])
        for name, label in (
            ("max_gross_notional", "最大名义敞口"),
            ("max_margin_fraction", "保证金 / 权益上限"),
            ("initial_margin_fraction", "初始保证金比例"),
            ("max_adverse_price_move_fraction", "授权不利价格变动上限"),
            ("order_lifetime_seconds", "授权有效时间（秒）"),
        )
    )
    options = ['<option value="">请选择已接受的数据</option>']
    for dataset in datasets:
        selection = (
            " selected"
            if selected is not None and (dataset["snapshot_id"] == selected["snapshot_id"])
            else ""
        )
        label = _dataset_label(dataset)
        options.append(
            f'<option value="{_text(dataset["snapshot_id"])}"{selection}>{_text(label)}</option>'
        )
    snapshot_field = (
        '<label>选择行情数据<select name="snapshot_id" required>'
        + "".join(options)
        + "</select></label>"
    )
    selected_link = (
        '<a id="selected-data-link" hidden>查看来源与质量详情</a>'
        if selected is None
        else f'<a id="selected-data-link" href="/datasets/{_text(selected["snapshot_id"])}">'
        "查看来源与质量详情</a>"
    )
    reuse_hidden = " hidden" if selected is None else ""
    selected_notice = "" if selected is None else _availability_notice(selected)
    return f"""<section class="intro"><p class="eyebrow">RESEARCH WORKSPACE</p>
<h1>让一段行情，成为可复核的研究。</h1><p>选择已保存的行情或导入新文件，查看扣费后的权益、持仓和每次风险决定。</p></section>
<section class="panel data-library"><div class="section-title"><h2>已接受的数据</h2>
<span class="muted">独立保存 · 无需先运行研究 · 最近 50 份</span></div>
{_dataset_list(datasets)}</section>
<div class="workspace"><section class="panel"><div class="section-title">
<span class="step">01</span><h2>上传原文并处理</h2></div>
<p class="muted">当前支持一个合约、一个交易日内的一个连续时段，1 分钟 bars。
填写实际合约属性和数据覆盖时段。</p>
<form id="import-form"><label class="file-input">CSV 文件
<input name="file" type="file" accept=".csv,text/csv" required></label>
<p class="muted">原文件最多 5 MiB，按收到的字节留存，不在浏览器中解码或转换。
数据层另检查归档总量和可用磁盘空间；处理失败也能在<a href="/sources">来源与处理</a>查询。</p>
<fieldset><legend>输入起点与明确许可</legend>
<label>实际上传的内容<select name="input_kind" required>
<option value="">请选择文件的真实起点</option>
<option value="RECEIVED_CSV">实际收到的 CSV（不宣称供应商原文）</option>
<option value="CONVERTED_CSV">外部转换后 CSV</option></select></label>
<label>用途与留存依据<textarea name="use_basis" rows="3" required
placeholder="写明来源条款或授权，以及本机研究、留存与备份用途。请勿上传无权留存的内容。"></textarea></label>
<label class="check-field"><input type="checkbox" name="allow_retention" required>
我确认有权将本次内容留存于应用归档，并用于本机研究及备份。</label>
<label class="check-field"><input type="checkbox" name="allow_download">
允许本机操作者下载此归档文件（不等于对外再分发许可）。</label>
<details><summary>已有上游归档与转换说明（可选）</summary>
<label>已托管上游来源身份<input name="upstream_source_id"
placeholder="已有来源详情中的 UUID"></label>
<label>转换说明<textarea name="transformation_note" rows="3"
placeholder="外部转换文件需说明转换方式；没有上游原文时明确缺失，不能补造血缘。"></textarea></label>
</details></fieldset>{_spec_fields()}
<details><summary>CSV 格式</summary><p>UTF-8，第一行包含以下列。
时间带明确时区；available_at 不得早于该分钟完成。</p>
<code class="csv-columns">{_CSV_COLUMNS}</code>
<p class="muted">一行对应一个完整分钟；volume 为手数。
合约、币种与时段使用上方填写的属性。</p></details>
<button type="submit">按原字节归档并处理</button>
<p id="import-status" role="status" class="status"></p></form></section>
<section class="panel"><div class="section-title">
<span class="step">02</span><h2>运行研究</h2></div>
<p class="muted">系统自动取得合约属性，并从模拟账本生成账户状态。
动量超过阈值后映射为明确的目标仓位。</p>
<form id="research-form">{snapshot_field}
<div class="data-selection-actions">{selected_link}
<button type="button" class="text-button" id="reuse-data-metadata"{reuse_hidden}>
复用合约与来源信息</button></div>
<div id="selected-data-notice">{selected_notice}</div>
<div class="fields">{basic_fields}</div><details><summary>风险限制</summary>
<div class="fields">{risk_fields}</div></details>
<button type="submit">运行整段研究</button>
<p id="research-status" role="status" class="status"></p></form></section></div>
<section class="panel history"><div class="section-title"><h2>研究记录</h2>
<span class="muted">保存完整配置与结果</span></div>
{_run_list(runs)}</section>"""


def _run_list(runs: list[dict[str, object]]) -> str:
    if not runs:
        return '<div class="empty">还没有研究记录。导入一段行情并运行后，结果会保存在这里。</div>'
    rows = []
    for run in runs:
        summary, market = _object(run["summary"]), _object(run["market"])
        rows.append(
            f'<tr><td><a href="/runs/{_text(run["run_id"])}">{_text(market["symbol"])}</a></td>'
            f"<td>{_text(run['created_at'])}</td><td>{_text(summary['bar_count'])}</td>"
            f"<td>{_percentage(summary['total_return'])}</td>"
            f"<td>{_percentage(summary['max_drawdown_fraction'])}</td>"
            f"<td>{_text(summary['fill_count'])}</td>"
            f'<td><button class="text-button" data-use-run="{_text(run["run_id"])}">'
            "用此配置</button></td></tr>"
        )
    return _table(["合约", "保存时间（UTC）", "Bars", "净收益率", "最大回撤", "成交", ""], rows)


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
