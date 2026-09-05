"""Present a fixed opening budget without turning arithmetic into execution permission."""

from __future__ import annotations

import json
from html import escape
from typing import cast

_STATUS = {
    "WITHIN_BUDGET": "固定输入下，一手数值在预算内（不可执行）",
    "REJECT": "一手预算不满足约束",
    "UNKNOWN": "证据不足，不能确定预算",
}
_AMOUNTS = {
    "side": "开仓方向（来自原影子目标）",
    "quantity_lots": "开仓手数（固定一手）",
    "requested_price": "所选限价（未发送）",
    "notional": "一手名义金额（CNY）",
    "margin_budget": "保证金预算（CNY）",
    "fee_budget": "手续费预算（CNY，非实际扣费）",
    "capital_budget": "合计资金预算（CNY，非实际冻结）",
    "reservation_price": "名义金额与手续费预算计价（非成交价）",
    "margin_reference_price": "保证金预算计价（涨停价与昨结算价取大）",
    "available_after_budget": "扣除预算后可用金额（CNY，非账户现值）",
}


def _text(value: object) -> str:
    return escape("未取得" if value is None else str(value), quote=True)


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _json(value: object) -> str:
    return _text(json.dumps(value, ensure_ascii=False, indent=2))


def _reasons(values: object) -> str:
    reasons = cast(list[str], values)
    return (
        '<ul class="budget-reasons">'
        + "".join(f"<li>{_text(reason)}</li>" for reason in reasons)
        + "</ul>"
    )


def panel(stream: dict[str, object], context: dict[str, object]) -> str:
    """Select only saved evidence; polling never changes these choices."""
    steps = cast(list[dict[str, object]], stream["steps"])
    options = []
    for step in steps:
        intent = _object(step["result"])["intent"]
        if intent is not None:
            options.append(
                f'<option value="{_text(step["sequence"])}">序号 {_text(step["sequence"])} · '
                f"目标比例 {_text(_object(intent)['target_fraction'])} · "
                f"{_text(step['committed_at'])}</option>"
            )
    checks = cast(list[dict[str, object]], context["order_checks"])
    check_options = "".join(
        f'<option value="{_text(check["check_id"])}">{_text(check["recorded_at"])} · '
        f"{_text(check['status'])}</option>"
        for check in checks
    )
    budgets = cast(list[dict[str, object]], context["budgets"])
    rows = "".join(
        f'<tr><td><a href="/broker/opening-budgets/{_text(item["budget_id"])}">'
        f"{_text(item['recorded_at'])}</a></td><td>{_text(item['sequence'])}</td>"
        f'<td class="wrap-cell">{_text(_STATUS.get(str(item["status"]), item["status"]))}</td></tr>'
        for item in budgets
    )
    if not rows:
        rows = '<tr><td colspan="3">尚无本地预算；未计算不等于没有风险。</td></tr>'
    disabled = "" if options and checks else " disabled"
    return f"""<section class="panel"><h2>从固定影子目标计算一手开仓预算</h2>
<p><strong>历史固定预算 · 不连接柜台，不报单或撤单。</strong>
选择已保存的影子步骤、同账户委托核对和明确限价；方向取自原目标，数量固定一手。
使用原配置与柜台证据，不允许输入账户资金、费率或方向，不复制研究初始资金。</p>
<p>即使数值在预算内，也不是当前账户已对账、执行许可或风险额度预占。
旧查询和旧行情仍保持原时刻；不能把此次计算时间当作资金或报价的新鲜时间。</p>
<form id="opening-budget-form" data-stream-id="{_text(stream["stream_id"])}">
<label>已保存的影子目标步骤<select name="sequence" required>
<option value="">选择本页已有目标</option>{"".join(options)}</select></label>
<label>同账户固定委托核对<select name="order_check_id" required>
<option value="">选择已有核对记录（不是执行许可）</option>{check_options}</select></label>
<label>一手开仓限价<input name="limit_price" type="text" inputmode="decimal"
maxlength="80" required placeholder="明确十进制限价；不会发送委托"></label>
<p class="muted">只能评估保存证据支持的范围，缺项不补零，不从净仓推测双向仓位。
没有候选时请查看已有影子结果及 <a href="/broker">账户核对</a>，此表单不会为预算建立连接。
轮询不改动选择；需要新步骤或核对时重新打开本页。</p>
<button type="submit"{disabled}>保存一手历史预算（不发单）</button>
<p id="opening-budget-status" class="status" role="status"></p></form>
<h3>最近 20 条固定预算</h3><div class="table-scroll"><table><thead><tr>
<th>记录时间（UTC）</th><th>影子来源序号</th><th>预算结果（非执行权限）</th>
</tr></thead><tbody>{rows}</tbody></table></div></section>"""


def report(result: dict[str, object]) -> str:
    raw_budget = result["budget"]
    if raw_budget is None:
        values = "<p>当前证据不足以计算数值，不以零金额或零风险替代。</p>"
    else:
        budget = _object(raw_budget)
        values = (
            '<dl class="identity">'
            + "".join(
                f"<dt>{_text(label)}</dt><dd>{_text(budget.get(field))}</dd>"
                for field, label in _AMOUNTS.items()
            )
            + "</dl>"
        )
    reasons = _reasons(result["reasons"]) if result["reasons"] else "<p>无附加预算原因。</p>"
    return f"""<section class="intro"><p class="eyebrow">SIMNOW · HISTORICAL BUDGET ONLY</p>
<h1>{_text(_STATUS.get(str(result["status"]), result["status"]))}</h1>
<p>固定影子步骤 {_text(result["sequence"])} · 记录于 {_text(result["recorded_at"])} UTC</p>
<a href="/streams/{_text(result["stream_id"])}">返回来源影子流</a></section>
<aside class="data-notice"><strong>本地预算，不是可执行订单或账户已对账。</strong>
<p>WITHIN_BUDGET 只说明固定历史输入下的数值约束；没有发送、模拟成交、资金冻结或预占释放。
账户与市场事实保留原时间，配置不因模板编辑改变，重新计算也不授予执行权。</p></aside>
<section class="panel"><h2>一手开仓的固定数值</h2>{values}
<h3>预算结果与原因</h3><p>{_text(result["status"])}</p>{reasons}
<p class="muted">保证金与手续费为预算而非已确认账目；未知费用不得写成已扣零元。
名义金额与手续费的预算计价：买开采用所选限价，卖开采用已确认涨停价。
保证金两方向均采用涨停价与昨结算价的较大值，不假定柜台按成交价收取；
保证金与手续费分别向上取整到分，不是实际扣费或冻结。
单独保存本结果，不覆盖原始查询、影子目标或既有持仓账簿。</p></section>
<section class="panel"><h2>为什么仍然不能执行</h2>
{_reasons(result["execution_blockers"])}
<p>报单能力：关闭。撤单能力：关闭。预算未创建任何发送身份或实际风险预占。
未决委托、未知成交和完整账户核对必须由后续执行流程处理。</p></section>
<section class="panel"><h2>固定来源与限制</h2>
<p><a href="/broker/{_text(result["query_batch_id"])}">查看账户与条款查询</a> ·
<a href="/api/broker/order-checks/{_text(result["order_check_id"])}">固定委托核对 JSON</a> ·
<a href="/api/broker/opening-budgets/{_text(result["budget_id"])}">本地完整预算 JSON</a></p>
{_reasons(result["limitations"])}
<details><summary>影子步骤、配置、账户、条款与原始时间证据</summary>
<pre>{_json(result["inputs"])}</pre></details>
<details><summary>完整固定预算与实现身份</summary><pre>{_json(result)}</pre></details>
</section>"""
