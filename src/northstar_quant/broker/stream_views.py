"""Explain durable reception and shadow decisions without implying execution."""

from __future__ import annotations

from html import escape
from typing import cast


def _text(value: object) -> str:
    return escape("未取得" if value is None else str(value), quote=True)


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


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
        account = _object(stream["account_progress"])
        account_cursor = "未绑定" if account["baseline_id"] is None else account["through_sequence"]
        rows.append(
            f'<tr><td><a href="/streams/{_text(stream["stream_id"])}">'
            f"{_text(stream['created_at'])}</a></td>"
            f"<td>{_text(_object(binding['profile'])['name'])}</td>"
            f"<td>{_text(binding['instrument'])}</td><td>{_text(stream['status'])}</td>"
            f"<td>{_text(stream['received'])} / {_text(stream['cursor'])} / "
            f"{_text(account_cursor)}</td>"
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
<p>若该环境/账户已有唯一固定基准，启动同时绑定本机账户入账；没有基准则保持未绑定，
仍可接收和观察影子策略。入账只处理已存账户回报，处理进度不代表完整对账或执行许可。</p>
<p>来源为 <code>COPIED_CTP_CALLBACKS_POSTGRESQL</code>：PostgreSQL 逐行保留实际复制的
SDK 白名单回调，不是网络原始字节，也不是已发布研究 Snapshot。</p>
<p>每段接收最多 7200 秒、100000 条回调、128 MiB。连接或持久化失败、
时段与新鲜度未知均需解释，不能把没有更新当作零成交或完成对账。</p>
<p class="muted">真实连续行情能力仍须在适当时段实证。应用在线、订阅成功或旧行情到达，
均不证明当前价格可用；本轮不提供自动重连、执行所有权或预占释放。</p></section></div>
<section class="panel"><h2>接收记录</h2><div class="table-scroll"><table><thead><tr>
<th>创建时间（UTC）</th><th>环境</th><th>合约</th><th>持久状态</th><th>已收 / 影子 / 账户序号</th>
<th>当前原因</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>"""
