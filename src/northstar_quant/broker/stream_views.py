"""Explain durable reception and shadow decisions without implying execution."""

from __future__ import annotations

import json
from html import escape
from typing import cast


def _text(value: object) -> str:
    return escape("未取得" if value is None else str(value), quote=True)


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _json(value: object) -> str:
    return _text(json.dumps(value, ensure_ascii=False, indent=2))


def workspace(
    queries: list[dict[str, object]],
    configurations: list[dict[str, object]],
    streams: list[dict[str, object]],
) -> str:
    query_options = "".join(
        f'<option value="{_text(query["batch_id"])}">'
        f"{_text(_object(query['profile'])['name'])} · {_text(query['instrument'])} · "
        f"账户 {_text(query['account_id'])} · {_text(query['created_at'])}</option>"
        for query in queries
        if query["status"] == "COMPLETE"
    )
    configuration_options = "".join(
        f'<option value="{_text(item["configuration_id"])}">'
        f"{_text(item['name'])} · {_text(item['created_at'])}</option>"
        for item in configurations
    )
    rows = []
    for stream in streams:
        binding = _object(stream["binding"])
        rows.append(
            f'<tr><td><a href="/streams/{_text(stream["stream_id"])}">'
            f"{_text(stream['created_at'])}</a></td>"
            f"<td>{_text(_object(binding['profile'])['name'])}</td>"
            f"<td>{_text(binding['instrument'])}</td><td>{_text(stream['status'])}</td>"
            f"<td>{_text(stream['received'])} / {_text(stream['cursor'])}</td>"
            f'<td class="wrap-cell">{_text(stream["reason"])}</td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="6">尚无持续接收记录。打开此页不会连接柜台。</td></tr>')
    disabled = "" if query_options and configuration_options else " disabled"
    return f"""<section class="intro"><p class="eyebrow">SIMNOW · SHADOW_ONLY</p>
<h1>接收真实回调，观察影子决策。</h1>
<p>一个已批准环境、一个账户、一个确认合约；先持久保存证据，再推进分钟策略。</p></section>
<aside class="data-notice"><strong>只读接收 · 无报单、撤单或真实账户风控授权</strong>
<p>影子目标不是委托。真实账户余额不参与此影子路径，也不创建模拟资金或成交。
只支持已核实的 SHFE DAY 分钟观察；夜盘、集合竞价、跨日与缺口不补做决策。</p></aside>
<div class="workspace"><section class="panel"><h2>明确启动一段接收</h2>
<form id="stream-create-form"><label>已保存的完整查询
<select name="query_batch_id" required><option value="">选择环境、账户与合约依据</option>
{query_options}</select></label>
<p class="muted">旧查询只固定连接范围和合约证据，不证明当前账户或行情已核对；
启动时仍核验实际身份、条款和可用性。没有候选？<a href="/broker">查看只读查询</a>。</p>
<label>固定策略配置<select name="configuration_id" required>
<option value="">选择已有不可变配置</option>{configuration_options}</select></label>
<p class="muted">此处只复用策略参数，不使用配置中的模拟资金、费用或保证金假设。
需要新修订时，<a href="/paper">先保存配置</a>；不热换活动接收中的配置。</p>
<label>最长接收秒数（60–7200）<input type="number" name="duration_seconds"
min="60" max="7200" step="1" value="300" required></label>
<label class="check-field"><input type="checkbox" name="allow_retention" required>
<span>我确认有权将本次 SDK 白名单回调及处理证据持久保留于本机。</span></label>
<label>使用与留存依据<textarea name="use_basis" rows="3" maxlength="500" required
placeholder="说明本次来源的用途与留存依据；不要填写密码、认证码或个人身份资料。"></textarea></label>
<button type="submit"{disabled}>连接并启动影子预热（不报撤单）</button>
<p id="stream-create-status" class="status" role="status"></p></form></section>
<section class="panel"><h2>接收、暂停与停止各自意味着什么</h2>
<p>启动会在已批准范围内建立 TD / MD 连接。暂停影子策略继续接收证据；
恢复重新预热，不能补做暂停期间的信号。停止结束本次连接，重试原命令不会重连。</p>
<p>来源为 <code>COPIED_CTP_CALLBACKS_POSTGRESQL</code>：PostgreSQL 逐行保留实际复制的
SDK 白名单回调，不是网络原始字节，也不是已发布研究 Snapshot。</p>
<p>每段接收最多 7200 秒、100000 条回调、128 MiB。连接或持久化失败、
时段与新鲜度未知均需解释，不能把没有更新当作零成交或完成对账。</p>
<p class="muted">真实连续行情能力仍须在适当时段实证。应用在线、订阅成功或旧行情到达，
均不证明当前价格可用；本轮不提供自动重连、执行所有权或预占释放。</p></section></div>
<section class="panel"><h2>接收记录</h2><div class="table-scroll"><table><thead><tr>
<th>创建时间（UTC）</th><th>环境</th><th>合约</th><th>持久状态</th><th>已收 / 已处理</th>
<th>当前原因</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>"""


def report(stream: dict[str, object]) -> str:
    binding, state = _object(stream["binding"]), _object(stream["state"])
    request = _object(binding["request"])
    identifier = _text(stream["stream_id"])
    active = stream["status"] in {"STARTING", "RECEIVING"}
    resume = stream["status"] == "RECEIVING" and stream["connection"] == "RECEIVING"
    pause_disabled = "" if active else " disabled"
    resume_disabled = "" if resume and stream["paused"] else " disabled"
    steps = cast(list[dict[str, object]], stream["steps"])
    rows = "".join(_step_row(step, identifier) for step in steps[:10])
    if not rows:
        rows = '<tr><td colspan="5">尚无完成的分钟或影子目标；未收到不等于零。</td></tr>'
    return f"""<section class="intro"><p class="eyebrow">SIMNOW · SHADOW_ONLY</p>
<h1>持续接收与分钟影子策略</h1><p>{_text(_object(binding["profile"])["name"])} ·
账户 {_text(binding["account_id"])} · {_text(binding["instrument"])}</p>
<a href="/streams">返回接收工作台</a></section>
<aside class="data-notice"><strong>SHADOW_ONLY · 不报单、不撤单、不构成账户风控或执行授权</strong>
<p>只记录来源与策略目标，没有真实账户资金投影、模拟成交或风险预占释放。
暂停不等于柜台没有风险，停止也不是撤单或平仓。</p></aside>
<div id="stream-report" data-stream-id="{identifier}"
data-stream-status="{_text(stream["status"])}">
<section class="panel"><h2>连接与影子推进</h2><dl class="identity">
<dt>持久状态</dt><dd id="stream-state">{_text(stream["status"])}</dd>
<dt>本机接收进程</dt><dd id="stream-connection">{_text(stream["connection"])}</dd>
<dt>影子推进</dt><dd id="stream-paused">
{"暂停或尚未满足推进条件" if stream["paused"] else "已启用，仍需逐条检查"}</dd>
<dt>原因</dt><dd id="stream-reason">{_text(stream["reason"])}</dd>
<dt>最近保护暂停</dt><dd id="stream-last-pause-reason">
{_text(state.get("last_pause_reason"))}</dd>
<dt>保护触发时间（UTC）</dt><dd id="stream-last-pause-at">
{_text(state.get("last_pause_at"))}</dd>
<dt>TD / MD 交易日</dt><dd id="stream-trading-days">
{_text(state.get("TD_trading_day"))} / {_text(state.get("MD_trading_day"))}</dd>
<dt>已接收 / 已处理</dt><dd id="stream-counts">
{_text(stream["received"])} / {_text(stream["cursor"])}</dd>
<dt>已存回调字节</dt><dd id="stream-bytes">{_text(stream["byte_count"])}</dd>
<dt>末次回调接收</dt><dd id="stream-last-received">{_text(state.get("last_received_at"))}</dd>
<dt>末次行情接收</dt><dd id="stream-last-market">{_text(state.get("last_market_received_at"))}</dd>
<dt>距末次接收（秒）</dt><dd id="stream-market-age">{_text(stream["market_age_seconds"])}</dd>
<dt>持久更新时间</dt><dd id="stream-updated">{_text(stream["updated_at"])}</dd></dl>
<p class="muted">接收间隔只描述本机收到时间，不证明来源行情新鲜。TD / MD 交易日与
ActionDay / UpdateTime 分别保留；接收进程存在不等于当前身份、市场或账户已核对。
最近保护暂停只记录自动保护触发，停止后仍保留，不代表最近一次操作者暂停或停止。</p>
<div class="actions"><button type="button" data-stream-control="PAUSE"{pause_disabled}>
暂停影子策略（继续接收）</button>
<button type="button" data-stream-control="RESUME"{resume_disabled}>恢复影子预热</button>
<button type="button" class="secondary" data-stream-control="STOP"{pause_disabled}>
停止本次连接</button></div><p id="stream-control-status" class="status" role="status"></p>
<p id="stream-poll-status" class="status" role="status">每秒只读更新当前状态，不重连柜台。</p>
<p class="muted">恢复会重置预热，不补做旧决策。STOP_REQUESTED 仅是已记录停止请求，
不是连接已经结束；终止或中断后的接收不能由 RESUME 重连。</p></section>
<section class="panel"><h2>最近 10 条分钟／影子结果</h2>
<div class="table-scroll"><table><thead><tr><th>来源序号</th><th>提交时间</th>
<th>已完成分钟</th><th>影子目标（不是委托）</th><th>原因</th></tr></thead>
<tbody id="stream-steps">{rows}</tbody></table></div>
<p class="muted">没有新结果不填零，不补造分钟。每步来源序号对应已持久回调，
当前查询未入账成交不用于自证账户完整或产生影子资金。</p></section>
<div><section class="panel"><h2>最新来源行情观察</h2>
<p id="stream-last-quote">{_quote_summary(state.get("last_market_data"))}</p>
<details><summary>原始 SDK 行情字段</summary>
<pre id="stream-last-data">{_json(state.get("last_market_data"))}</pre></details>
<p class="muted">保留 SDK 字段原义；旧行情或日期不一致不改写成当前时刻。</p></section>
<section class="panel"><h2>分钟处理与预热状态</h2>
<p id="stream-warmup">{_warmup_summary(state.get("market"))}</p>
<details><summary>完整分钟处理与预热证据</summary>
<pre id="stream-market-state">{_json(state.get("market"))}</pre></details></section></div>
{_archive_panel(stream)}
<section class="panel"><h2>固定输入与证据</h2>
<p><a href="/broker/{_text(request["query_batch_id"])}">来源账户／合约查询</a> ·
<a href="/api/streams/{identifier}">当前接收记录 JSON</a> ·
<a href="/api/streams/{identifier}/events?after=0">最初 100 条实际回调证据</a></p>
<p>实际 SDK 回调以 PostgreSQL 不可变逐行记录为准，不是网络原文或历史可得性证明。
只有归档加工通过后才有独立发布的研究产品；查看 JSON 与翻页不连接柜台。</p>
<details><summary>固定配置、使用依据、来源与实现身份</summary>
<pre>{_json(binding)}</pre></details></section></div>"""


def _archive_panel(stream: dict[str, object]) -> str:
    received = cast(int, stream["received"])
    disabled = "" if received else " disabled"
    attempts = cast(list[dict[str, object]], stream["archives"])
    rows = "".join(_archive_row(attempt) for attempt in attempts)
    if not rows:
        rows = '<tr><td colspan="5">尚无本地归档加工尝试；未归档不等于已发布。</td></tr>'
    return f"""<section class="panel"><h2>将固定回调前缀归档并加工为市场段</h2>
<p>只读取已保存的来源，不连接柜台。归档为带原接收时刻的 CTP 回调 JSON，
不是 CSV，也不是供应商网络字节；从完整回调重新计算分钟，不复制近期影子结果。
加工不会回写已运行的影子目标，也不补做旧控制期间的决策。</p>
<form id="stream-archive-form" data-stream-id="{_text(stream["stream_id"])}">
<label>固定前缀：从序号 1 到
<input name="through_sequence" type="number" min="1" max="{received}" step="1"
value="{received}" required></label>
<p class="muted">本页打开时已保存 {received} 条。轮询不扩大所选前缀；
需选择后来收到的内容时重新打开页面。整个 JSON 前缀最多 5 MiB，超限明确拒绝，不悄悄截断。</p>
<label>首分钟起点（UTC）<input name="session_open" type="text" required maxlength="40"
placeholder="YYYY-MM-DDTHH:MM:00Z"></label>
<label>最后一分钟完成边界（UTC）<input name="session_close" type="text" required maxlength="40"
placeholder="YYYY-MM-DDTHH:MM:00Z"></label>
<p class="muted">明确选择左闭右开的完整分钟范围，UTC 不由浏览器本地时区推测。
首个部分分钟、末尾未完成分钟和缺口不能靠缩放时间或补零变成有效数据；
源日期、接收时刻与归档时间分别保留。只发布满足当前 DAY 质量约束的市场段。</p>
<label class="check-field"><input name="allow_download" type="checkbox">
<span>另行允许本机下载此归档：JSON 包含账户 TD 回调和私有身份，
默认不允许下载，不应公开或转发。</span></label>
<button type="submit"{disabled}>本地归档并检查发布条件（不连接柜台）</button>
<p id="stream-archive-status" class="status" role="status"></p></form>
<h3>最近 50 次归档与加工</h3><div class="table-scroll"><table><thead><tr>
<th>固定前缀</th><th>所选 UTC 范围</th><th>加工状态与来源</th><th>发布产品</th><th>原因</th>
</tr></thead><tbody id="stream-archives">{rows}</tbody></table></div></section>"""


def _archive_row(attempt: dict[str, object]) -> str:
    parameters = _object(attempt["parameters"])
    snapshot = attempt["snapshot_id"]
    product = (
        "尚未发布"
        if snapshot is None
        else f'<a href="/datasets/{_text(snapshot)}">查看已发布数据</a>'
    )
    return (
        f"<tr><td>1–{_text(parameters['through_sequence'])}</td>"
        f'<td class="wrap-cell">{_text(parameters.get("session_open"))} → '
        f"{_text(parameters.get('session_close'))}</td>"
        f'<td><a href="/attempts/{_text(attempt["attempt_id"])}">'
        f"{_text(attempt['status'])}</a><br>"
        f'<a href="/sources/{_text(attempt["source_id"])}">托管 JSON 来源</a></td>'
        f'<td>{product}</td><td class="wrap-cell">{_text(attempt["error"] or "—")}</td></tr>'
    )


def _step_row(step: dict[str, object], identifier: str) -> str:
    result = _object(step["result"])
    return (
        f'<tr><td><a href="/api/streams/{identifier}/events?'
        f'after={cast(int, step["sequence"]) - 1}">{_text(step["sequence"])}</a></td>'
        f"<td>{_text(step['committed_at'])}</td>"
        f'<td class="wrap-cell">{_bar_summary(result["bar"])}</td>'
        f'<td class="wrap-cell">{_intent_summary(result["intent"])}'
        f"<details><summary>本步完整证据</summary><pre>{_json(step)}</pre></details></td>"
        f'<td class="wrap-cell">{_text(result["reason"])}</td></tr>'
    )


def _bar_summary(value: object) -> str:
    if value is None:
        return "本步没有完成分钟"
    bar = _object(value)
    return (
        f"<p>{_text(bar['start_at'])} → {_text(bar['completed_at'])}</p>"
        f"<p>开 {_text(bar['open'])} · 高 {_text(bar['high'])} · "
        f"低 {_text(bar['low'])} · 收 {_text(bar['close'])}</p>"
        f"<p>采样累计差分量 {_text(bar['volume'])}</p>"
    )


def _intent_summary(value: object) -> str:
    if value is None:
        return "本步没有影子目标"
    intent = _object(value)
    momentum = str(intent["momentum"])
    if len(momentum) > 14:
        momentum = momentum[:14] + "…"
    return (
        f"<p>账户中性目标比例 {_text(intent['target_fraction'])}</p>"
        f"<p>动量 {_text(momentum)}</p><p>有效至 {_text(intent['valid_until'])}</p>"
    )


def _quote_summary(value: object) -> str:
    if value is None:
        return "尚未收到行情，不以零价替代。"
    quote = _object(value)
    return (
        f"源日期 {_text(quote.get('ActionDay'))} · {_text(quote.get('UpdateTime'))} · "
        f"最新价 {_text(quote.get('LastPrice'))} · 数量字段 {_text(quote.get('Volume'))}"
    )


def _warmup_summary(value: object) -> str:
    if value is None:
        return "尚无可处理行情，或影子预热已重置。"
    market = _object(value)
    return f"{_text(market['status'])} · {_text(market['reason'])}"
