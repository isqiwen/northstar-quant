"""Render saved broker facts; opening a page never connects to the counter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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


def report(
    batch: dict[str, object],
    baseline_context: dict[str, object],
    ledger_context: dict[str, object],
) -> str:
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
        if name == "instrument":
            note += " 目标合约须按代码精确匹配；包含同前缀合约在内的全部回包保存在原始记录中。"
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
<p>各查询分时返回，不构成同一时刻的原子账户快照。完整的资金、费用与结算账本尚未建立，
原始查询自身不提供账本核对。下方观察比较、成交入账及数量比较独立保存，不改写原回包，
不会把未知写成零，也不会覆盖研究或 Paper 账本。</p></aside>
{_baseline_panel(batch, baseline_context)}
{_ledger_panel(batch, baseline_context, ledger_context)}
<section class="panel"><h2>查询范围与证据</h2><p>命令身份 {_text(batch["batch_id"])}</p>
<p>交易账户身份 {_text(completeness["identity"])} ·
柜台交易日 {_text(completeness["trading_day"])}</p>
{_json(capture_info)}<details><summary>失败、缺项与未完成对账的原因</summary>
{_json(batch["reconciliation"])}</details>
<details><summary>环境、查询范围与实现身份</summary>
{_json({key: batch[key] for key in ("profile", "query_scope", "implementation_hash")})}</details>
<a href="/api/broker/queries/{_text(batch["batch_id"])}">查看完整固定回包记录（本机私有）</a>
</section><div class="workspace">{"".join(panels)}</div>
<section class="panel"><h2>行情观察</h2><p>仅本次有限连接的订阅与行情证据，
不是持续行情服务；无行情不代表价格为零，行情登录也不作为交易账户身份凭据。</p>
{_json(batch["market"])}</section>"""


def _baseline_panel(batch: dict[str, object], context: dict[str, object]) -> str:
    eligibility = _object(context["eligibility"])
    baseline = context["baseline"]
    checks = cast(list[dict[str, object]], context["checks"])
    query_id = _text(batch["batch_id"])
    if baseline is None:
        disabled = "" if eligibility["allowed"] else " disabled"
        reasons = "".join(
            f"<li>{_text(reason)}</li>" for reason in cast(list[str], eligibility["reasons"])
        )
        explanation = (
            "<p>本次完整空账户观察可固定为基准。一个账户只保留一份，不能重新建立来消除差异。</p>"
            if eligibility["allowed"]
            else f"<p>当前观察不能建立空账户基准。</p><ul>{reasons}</ul>"
        )
        content = f"""{explanation}
<button type="button" data-broker-local="establish" data-query-batch-id="{query_id}"{disabled}>
固定本次观察为基准（仅本地）</button>"""
    else:
        baseline = _object(baseline)
        source_id = _text(baseline["source_batch_id"])
        funds = _object(_object(baseline["opening"])["funds"])
        rows = "".join(
            f"<tr><th>{_text(field)}</th><td>{_text(value)}</td></tr>"
            for field, value in funds.items()
        )
        timing_note = _query_timing_note(batch, baseline["recorded_at"])
        button = (
            "<p>这是基准来源查询，不能与自身比较。需要基准固定之后的独立查询记录。</p>"
            if batch["batch_id"] == baseline["source_batch_id"]
            else "<p>本次查询已有固定比较，见下方结果；不会重复建立比较。</p>"
            if any(check["query_batch_id"] == batch["batch_id"] for check in checks)
            else f"<p>{_text(timing_note)}</p>"
            if timing_note is not None
            else f'<button type="button" data-broker-local="compare" '
            f'data-query-batch-id="{query_id}" '
            f'data-baseline-id="{_text(baseline["baseline_id"])}">'
            "将本次查询与固定基准比较（仅本地）</button>"
        )
        content = f"""<p>已固定 · {_text(baseline["recorded_at"])} ·
柜台交易日 {_text(baseline["trading_day"])} · {_text(baseline["currency"])}</p>
<p><a href="/broker/{source_id}">查看基准来源查询</a>；
基准不随之后的查询变化，不能重建来清除差异。</p>
{button}<details><summary>固定的空账户资金观察</summary>
<div class="table-scroll"><table><thead><tr><th>柜台字段</th><th>基准值</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<details><summary>基准身份与来源摘要</summary>{_json(baseline)}</details></details>"""
    history = "".join(_baseline_check(check, batch["batch_id"]) for check in checks)
    if not history:
        history = "<p>尚无独立查询比较。保存基准本身不产生“一致”结果。</p>"
    return f"""<section class="panel" id="broker-baseline-panel">
<h2>本地空账户观察基准</h2>
<p>只比较已保存的柜台观察，不连接柜台、不下单；不接受手工资金或仓位。
范围仅为同一账户的空仓人民币观察，不是成交、资金流或结算账本。</p>
{content}<p id="broker-baseline-status" class="status" role="status"></p>
<p class="muted">基准固定后，请另行发起一次已授权的只读查询，再在新记录中执行比较。
这里的按钮不会代替你发起查询，也不会把原命令重试当作独立证据。</p>
<h3>最近的观察比较（最多 20 条）</h3>{history}
<p class="muted">字段变化不直接代表损失；“未发现变化”也不是账本对账通过。
所有结果仍为 UNRECONCILED，均不开放报单、撤单或实盘权限。</p>
<a href="/api/broker/queries/{query_id}/baseline-context">查看独立基准与比较记录</a>
</section>"""


def _baseline_check(check: dict[str, object], current_query_id: object) -> str:
    label = {
        "MATCHED": "未发现字段变化（仅观察比较）",
        "DIFFERENCES": "观察字段或账户活动发生变化",
        "UNKNOWN": "信息不足，不能判断一致",
    }[str(check["status"])]
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{_text('未知' if item[field] is None else item[field])}</td>"
            for field in ("field", "expected", "observed", "delta")
        )
        + "</tr>"
        for item in cast(list[dict[str, object]], check["funds"])
    )
    opened = " open" if check["query_batch_id"] == current_query_id else ""
    reasons = "".join(f"<li>{_text(reason)}</li>" for reason in cast(list[str], check["reasons"]))
    return f"""<details{opened}><summary>{_text(label)} · {_text(check["created_at"])}</summary>
<p><a href="/broker/{_text(check["query_batch_id"])}">查看比较使用的独立查询</a></p>
<div class="table-scroll"><table><thead><tr><th>柜台字段</th><th>固定基准</th>
<th>后续查询</th><th>字段变化</th></tr></thead><tbody>{rows}</tbody></table></div>
<h4>全账户持仓、委托与成交观察</h4>
<p class="muted">null 表示未知，不等于没有活动。</p>{_json(check["activity"])}
<ul>{reasons}</ul><a href="/api/broker/baseline-checks/{_text(check["check_id"])}">
查看固定比较证据（非账本对账）</a></details>"""


def _query_timing_note(batch: dict[str, object], recorded_at: object) -> str | None:
    """Explain visibly impossible timing; the owning Module still enforces commands."""

    if batch["capture"] is None:
        return "本次查询尚无固定终态，不能入账或作为独立比较证据。"
    capture = _object(batch["capture"])
    fixed = datetime.fromisoformat(str(recorded_at))
    if (
        datetime.fromisoformat(str(batch["created_at"])) <= fixed
        or datetime.fromisoformat(str(capture["started_at"])) <= fixed
    ):
        return "本次查询在目标记录固定前已开始，不能作为独立后续证据；请使用之后发起的查询。"
    if datetime.fromisoformat(str(capture["finished_at"])) >= datetime.now(UTC):
        return "本次查询结束时间尚未到达，不能入账或作为已完成的比较证据。"
    return None


def _ledger_panel(
    batch: dict[str, object],
    baseline_context: dict[str, object],
    context: dict[str, object],
) -> str:
    query_id = _text(batch["batch_id"])
    baseline_id = context["baseline_id"]
    baseline = baseline_context["baseline"]
    current, source_entry = context["current"], context["source_entry"]
    if baseline_id is None or baseline is None:
        action = "<p>先从完整空账户观察固定一次基准；不能手工补填成交或持仓作为起点。</p>"
    elif source_entry is not None:
        saved = _object(source_entry)
        action = (
            f"<p>本次查询已有第 {_text(saved['ordinal'])} 次固定入账："
            f'<a href="/api/broker/position-entries/{_text(saved["entry_id"])}">'
            "查看原入账记录</a>。不会再次导入或覆盖结果。</p>"
        )
    elif batch["batch_id"] == _object(baseline)["source_batch_id"]:
        action = "<p>本次查询是空账户基准来源；成交入账需要基准固定之后发起的查询。</p>"
    elif timing_note := _query_timing_note(batch, _object(baseline)["recorded_at"]):
        action = f"<p>{_text(timing_note)}</p>"
    else:
        action = f"""<button type="button" data-broker-ledger="ingest"
data-query-batch-id="{query_id}" data-baseline-id="{_text(baseline_id)}">
将本次保存的成交入账（仅本地）</button>
<p class="muted">按固定来源识别和去重成交；不会用查询汇总持仓覆盖账簿。
若需核对已有入账结果，请先执行下面的独立比较，再决定是否接收新的成交事实。</p>"""
    if current is None:
        projection = "<p>尚无成交入账记录；没有账簿不能声称持仓一致。</p>"
        comparison = ""
    else:
        latest = _object(current)
        latest_projection = _object(latest["position_projection"])
        projection = (
            f"<h3>最新固定投影 · 第 {_text(latest['ordinal'])} 次</h3>"
            f"<p>{_text(latest['status'])} · {_text(latest['recorded_at'])}</p>"
            f"<p>累计已识别成交 {_text(latest['fill_count'])} 笔；"
            f"持仓推导 {_text(latest_projection['status'])}。</p>"
            + _position_projection(latest_projection)
        )
        if context["current_check"] is not None:
            checked = _object(context["current_check"])
            comparison = (
                "<p>本次查询与最新入账结果已有固定比较："
                f'<a href="/api/broker/position-checks/{_text(checked["check_id"])}">'
                "查看原比较记录</a>；不会重复比较。</p>"
            )
        elif latest["source_batch_id"] == batch["batch_id"]:
            comparison = (
                "<p>本次查询是最新入账的来源，不能自行证明一致。"
                "需要该记录固定之后才发起的另一份查询。</p>"
            )
        elif timing_note := _query_timing_note(batch, latest["recorded_at"]):
            comparison = f"<p>{_text(timing_note)}</p>"
        else:
            comparison = f"""<button type="button" data-broker-ledger="compare"
data-query-batch-id="{query_id}" data-entry-id="{_text(latest["entry_id"])}">
将本次查询与固定账簿数量比较（仅本地）</button>
<p class="muted">只接受这次入账固定之后才发起的独立查询；早先的查询不能作为后验核对。
本次查询的新成交尚未入账时保留差异，不自动修改账簿消除它。</p>"""
    entries = "".join(
        _position_entry(entry, batch["batch_id"])
        for entry in cast(list[dict[str, object]], context["entries"])
    )
    checks = "".join(
        _position_check(check, batch["batch_id"])
        for check in cast(list[dict[str, object]], context["checks"])
    )
    return f"""<section class="panel" id="broker-ledger-panel">
<h2>成交入账与持仓数量核对</h2>
<p>当前范围：同一交易日、空账户起点、SHFE 期货投机持仓；
按真实保存的成交开平推导多空与今昨仓，不接受手工成交或仓位，不产生模拟成交。</p>
{action}{projection}{comparison}
<p id="broker-ledger-status" class="status" role="status"></p>
<p class="muted">这些按钮只处理本地证据，不连接柜台或触发新查询。
费用、资金流和结算账本尚未建立；READY 仅表示该次持仓可推导，
MATCHED 仅表示有限数量相同。所有结果仍为 UNRECONCILED，不启用报单、撤单或实盘权限。</p>
<h3>最近成交入账（最多 20 条）</h3>{entries or "<p>尚无固定入账记录。</p>"}
<h3>最近持仓数量比较（最多 20 条）</h3>{checks or "<p>尚无独立数量比较。</p>"}
<a href="/api/broker/queries/{query_id}/ledger-context">查看账簿记录与比较证据（本机私有）</a>
</section>"""


def _position_projection(projection: dict[str, object]) -> str:
    if projection["status"] != "KNOWN":
        return "<p>数量投影未知，不能将空列表解释为空仓；请查看入账问题。</p>"
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{_text(item[field])}</td>"
            for field in (
                "exchange",
                "symbol",
                "hedge_flag",
                "direction",
                "today_lots",
                "yesterday_lots",
            )
        )
        + "</tr>"
        for item in cast(list[dict[str, object]], projection["positions"])
    )
    if not rows:
        return "<p>按已入账成交推导为空仓；不是柜台当前空仓的独立证明。</p>"
    return f"""<div class="table-scroll"><table><thead><tr><th>交易所</th><th>合约</th>
<th>投保标志</th><th>方向</th><th>今仓手数</th><th>昨仓手数</th></tr></thead>
<tbody>{rows}</tbody></table></div>"""


def _position_entry(entry: dict[str, object], current_query_id: object) -> str:
    opened = " open" if entry["source_batch_id"] == current_query_id else ""
    return f"""<details{opened}><summary>第 {_text(entry["ordinal"])} 次入账 ·
{_text(entry["status"])} · {_text(entry["recorded_at"])}</summary>
<p>新识别 {_text(entry["new_fill_count"])} 笔，重复观察 {_text(entry["duplicate_count"])} 笔；
累计 {_text(entry["fill_count"])} 笔。</p>
<p><a href="/broker/{_text(entry["source_batch_id"])}">查看入账来源查询</a></p>
{_position_projection(_object(entry["position_projection"]))}
<h4>入账问题</h4>{_json(entry["problems"])}
<details><summary>本次新增的已识别成交</summary>{_json(entry["added_fills"])}</details>
<a href="/api/broker/position-entries/{_text(entry["entry_id"])}">查看固定入账证据</a></details>"""


def _position_check(check: dict[str, object], current_query_id: object) -> str:
    label = {
        "MATCHED": "数量相同（仅持仓数量范围）",
        "DIFFERENCES": "持仓数量或未入账成交存在差异",
        "UNKNOWN": "事实不足，不能判断持仓一致",
    }[str(check["status"])]
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{_text('未知' if item[field] is None else item[field])}</td>"
            for field in (
                "symbol",
                "direction",
                "expected_today",
                "observed_today",
                "delta_today",
                "expected_yesterday",
                "observed_yesterday",
                "delta_yesterday",
            )
        )
        + "</tr>"
        for item in cast(list[dict[str, object]], check["positions"])
    )
    if not rows:
        rows = '<tr><td colspan="8">没有逐合约数量；是否已知请以状态与问题为准。</td></tr>'
    opened = " open" if check["query_batch_id"] == current_query_id else ""
    activity = {
        "unrecorded_fills": check["unrecorded_fills"],
        "observed_orders": check["observed_orders"],
    }
    return f"""<details{opened}><summary>{_text(label)} · {_text(check["recorded_at"])}</summary>
<p><a href="/api/broker/position-entries/{_text(check["entry_id"])}">查看固定入账依据</a> ·
<a href="/broker/{_text(check["query_batch_id"])}">查看独立后续查询</a></p>
<div class="table-scroll"><table><thead><tr><th>合约</th><th>方向</th><th>账簿今仓</th>
<th>柜台今仓</th><th>今仓差</th><th>账簿昨仓</th><th>柜台昨仓</th><th>昨仓差</th>
</tr></thead><tbody>{rows}</tbody></table></div>
<h4>比较问题</h4>{_json(check["problems"])}
<details><summary>观察到的未入账成交与委托</summary>
<p>null 代表未知；这里只保留观察，不自动入账、报撤单或释放预占。</p>
{_json(activity)}
</details><a href="/api/broker/position-checks/{_text(check["check_id"])}">查看固定数量比较证据</a>
</details>"""
