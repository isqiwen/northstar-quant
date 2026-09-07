from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast
from uuid import UUID

from nicegui import ui
from nicegui.events import TableSelectionEventArguments

if TYPE_CHECKING:
    from northstar_quant.web.stream.page import _StreamPage


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _items(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


def _text(value: object) -> str:
    return "未取得" if value is None else str(value)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _table(identifier: str, columns: dict[str, str]) -> ui.table:
    return (
        ui.table(
            columns=[
                {"name": name, "label": label, "field": name, "align": "left"}
                for name, label in columns.items()
            ],
            rows=[],
            row_key="id",
            pagination=10,
        )
        .props(f'data-testid="{identifier}" flat bordered wrap-cells')
        .style("width:100%;max-width:100%")
    )


def _evidence(title: str, value: object = None, *, identifier: str) -> ui.label:
    with ui.expansion(title).classes("full-width"):
        return (
            ui.label(_json(value))
            .props(f'data-testid="{identifier}"')
            .style(
                "white-space:pre-wrap;overflow-wrap:anywhere;font-family:monospace;max-width:100%"
            )
        )


def _step_row(step: dict[str, object]) -> dict[str, object]:
    result = _object(step["result"])
    bar, intent = result["bar"], result["intent"]
    bar_text = (
        "本步没有完成分钟"
        if bar is None
        else (
            f"{_object(bar)['start_at']} → {_object(bar)['completed_at']} · "
            + " / ".join(_text(_object(bar)[key]) for key in ("open", "high", "low", "close"))
            + f" · 采样差分量 {_object(bar)['volume']}"
        )
    )
    intent_text = (
        "本步没有影子目标"
        if intent is None
        else (
            f"目标 {_object(intent)['target_fraction']} · 动量 {_object(intent)['momentum']} · "
            f"有效至 {_object(intent)['valid_until']}"
        )
    )
    return {
        "id": str(step["sequence"]),
        "sequence": step["sequence"],
        "at": step["committed_at"],
        "bar": bar_text,
        "intent": intent_text,
        "reason": _text(result["reason"]),
    }


def _archive_row(item: dict[str, object]) -> dict[str, object]:
    parameters = _object(item["parameters"])
    return {
        "id": item["attempt_id"],
        "prefix": f"1–{parameters['through_sequence']}",
        "range": f"{parameters['session_open']} → {parameters['session_close']}",
        "status": item["status"],
        "product": "尚未发布" if item["snapshot_id"] is None else "已发布",
        "reason": _text(item["error"]),
    }


def _budget_status(value: object) -> str:
    return {
        "WITHIN_BUDGET": "一手数值在预算内（不可执行）",
        "REJECT": "一手预算不满足约束",
        "UNKNOWN": "证据不足，不能确定预算",
    }.get(str(value), str(value))


def build(
    self: _StreamPage,
    stream: dict[str, object],
    budget_context: dict[str, object],
    ledger_context: dict[str, object],
) -> None:
    self.current = stream
    self.upper = cast(int, stream["received"])
    self.baseline = (
        _object(stream["account_progress"])["baseline_id"] or ledger_context["baseline_id"]
    )
    binding = _object(stream["binding"])
    ui.add_css("""
        .northstar-stream-page {width:100%;max-width:1280px;margin:0 auto;min-width:0}
        .northstar-stream-page .q-card {width:100%;min-width:0}
        .northstar-stream-page .q-field {width:100%;min-width:0}
        .northstar-stream-page .q-table {min-width:720px}
        .northstar-stream-page .q-table td {white-space:normal;overflow-wrap:anywhere}
        .northstar-stream-page .q-btn {max-width:100%;white-space:normal}
        .northstar-stream-page .q-expansion-item {min-width:0}
    """)
    with ui.column().classes("northstar-stream-page q-pa-md q-gutter-md"):
        ui.link("← 返回持续接收工作台", "/streams")
        ui.label("持续接收与分钟影子策略").classes("text-h4")
        ui.badge("SIMNOW · SHADOW_ONLY · 不报单、不撤单", color="orange-9")
        ui.label(
            f"{_text(_object(binding['profile'])['name'])} · "
            f"账户 {_text(binding['account_id'])} · {_text(binding['instrument'])}"
        ).style("overflow-wrap:anywhere")
        ui.label("暂停仍接收与入账；停止不是撤单或平仓。影子目标和历史预算均无执行权限。")
        self.notice = ui.label().props('data-testid="stream-command-status" role="status"')
        self.command_lookup = ui.link("按固定命令身份查询结果（只读）", "#").props(
            'data-testid="stream-command-lookup"'
        )
        self.command_lookup.set_visibility(False)
        self.poll_notice = ui.label("每秒只读更新状态，不连接柜台。 ").props(
            'data-testid="stream-poll-status" role="status"'
        )
        with ui.card():
            ui.label("连接与影子推进").classes("text-h6")
            _fields(
                self,
                {
                    "state": "持久状态",
                    "connection": "Live 接收进程",
                    "runtime": "目标 Live 运行身份",
                    "observed": "Live 状态观察时间",
                    "paused": "影子推进",
                    "reason": "原因",
                    "received": "已持久接收至序号",
                    "shadow-cursor": "影子输入检查至序号",
                    "trading-days": "TD / MD 交易日",
                    "bytes": "已存回调字节",
                    "last-received": "末次回调接收",
                    "last-market": "末次行情接收",
                    "market-age": "距末次行情接收（秒）",
                    "updated": "持久更新时间",
                    "last-pause-reason": "最近保护暂停",
                    "last-pause-at": "保护触发时间",
                },
            )
            with ui.row().classes("q-gutter-sm"):
                for action, label in {
                    "PAUSE": "暂停影子策略（继续接收）",
                    "RESUME": "恢复影子预热",
                    "STOP": "停止本次连接",
                }.items():
                    self.controls[action] = ui.button(
                        label, on_click=lambda action=action: self.control(action)
                    ).props(f'data-testid="stream-control-{action.lower()}"')
            ui.label(
                "连接存在不证明账户已核对；接收间隔不证明来源新鲜。恢复清空预热、"
                "不补旧决策。STOP_REQUESTED 是请求，STOPPED 才是结束。"
            ).classes("text-caption")
        with ui.card():
            ui.label("最近 10 条分钟／影子结果").classes("text-h6")
            self.steps = _table(
                "stream-steps",
                {
                    "sequence": "来源序号",
                    "at": "提交时间",
                    "bar": "已完成分钟",
                    "intent": "影子目标（不是委托）",
                    "reason": "原因",
                },
            )
            self.steps.selection = "single"
            self.steps.on_select(self.select_step)
            self.step_source = ui.link("查看所选步骤来源", "#")
            self.step_source.set_visibility(False)
            self.step_detail = _evidence("所选步骤完整证据", identifier="stream-step-evidence")
            ui.label("尚无结果时不填零，不补造分钟；选择一行可查看保存证据。")
        with ui.card():
            ui.label("行情观察与预热").classes("text-h6")
            self.quote = ui.label().props('data-testid="stream-last-quote"')
            self.quote_detail = _evidence("原始 SDK 行情字段", identifier="stream-last-data")
            self.warmup = ui.label().props('data-testid="stream-warmup"')
            self.market_detail = _evidence(
                "完整分钟处理与预热证据", identifier="stream-market-state"
            )
        _account(self, ledger_context)
        _budget(self, budget_context, stream)
        _archive(self)
        with ui.card():
            ui.label("固定输入与证据").classes("text-h6")
            ui.link("来源账户／合约查询", f"/broker/{self.query_id}")
            ui.link("当前接收记录 JSON", f"/api/streams/{self.identifier}")
            ui.link("最初 100 条回调证据", f"/api/streams/{self.identifier}/events?after=0")
            _evidence("固定配置、使用依据及实现身份", binding, identifier="stream-binding")
    self.timer = ui.timer(1, self.refresh, immediate=False)
    client = ui.context.client
    client.on_connect(self.connect)
    client.on_disconnect(self.disconnect)
    client.on_delete(self.delete)
    render(self, stream)


def _fields(self: _StreamPage, fields: dict[str, str]) -> None:
    with ui.element("div").classes("row full-width q-col-gutter-sm"):
        for key, title in fields.items():
            with ui.column().classes("col-12 col-sm-6 col-md-4"):
                ui.label(title).classes("text-caption text-grey-7")
                self.labels[key] = (
                    ui.label()
                    .props(f'data-testid="stream-{key}"')
                    .style("overflow-wrap:anywhere;max-width:100%")
                )


def _account(self: _StreamPage, context: dict[str, object]) -> None:
    with ui.card():
        ui.label("账户回报入账与本机处理进度").classes("text-h6")
        _fields(
            self,
            {
                "account-baseline": "本流固定账户基准",
                "account-cursor": "账户处理至来源序号",
                "account-pending": "尚待处理的已存回调",
                "account-state": "本机处理状态",
                "account-reason": "处理原因",
            },
        )
        self.entry_link = ui.link("查看本流最后固定入账", "#").props(
            'data-testid="stream-account-entry"'
        )
        ui.label(
            "UNRECONCILED · 仅已保存异步账户回报。READY 或游标追平不代表完整账户对账；"
            "积压不是外部漏报数量。启动查询、资金观察和委托核对尚未汇合。"
        )
        if self.baseline is None:
            ui.label("尚无可用账户基准，请先在来源查询页固定完整空基准。")
        else:
            ui.label(f"补处理固定基准：{self.baseline}").style("overflow-wrap:anywhere")
            self.account_upper = ui.input(
                "处理到本页已保存的来源序号", value=str(self.upper)
            ).props('data-testid="stream-account-through" inputmode="numeric" maxlength="6"')
            button = ui.button("补处理已保存账户回报（仅本地）", on_click=self.catchup).props(
                'data-testid="stream-account-submit"'
            )
            self.actions.append((button, self.upper > 0))
            ui.label(
                f"本页上界固定为 {self.upper}，不随刷新增加。补处理不连接、不补策略，"
                "首次补处理可绑定上述已有基准，之后不可更换。"
            ).classes("text-caption")
        ui.link("账户查询、资金观察与独立核对", f"/broker/{self.query_id}")
        self.entries = _table(
            "stream-account-entries",
            {
                "ordinal": "入账序号",
                "status": "状态",
                "fills": "新增／重复／累计成交",
                "source": "固定来源",
                "at": "记录时间",
            },
        )
        self.entries.selection = "single"
        self.entries.on_select(self.select_entry)
        self.entry_source = ui.link("查看所选入账 JSON", "#")
        self.entry_source.set_visibility(False)
        self.position_status = ui.label("选择入账记录查看当时的固定持仓；未取得不等于空仓。")
        self.positions = _table(
            "stream-positions",
            {
                "symbol": "合约",
                "direction": "方向",
                "today": "今仓",
                "yesterday": "昨仓",
            },
        )
        self.entry_detail = _evidence(
            "所选入账的持仓、成交与问题", identifier="stream-entry-evidence"
        )
        render_entries(self, context)


def _budget(self: _StreamPage, context: dict[str, object], stream: dict[str, object]) -> None:
    steps = {
        str(step["sequence"]): (
            f"序号 {step['sequence']} · 目标 "
            f"{_object(_object(step['result'])['intent'])['target_fraction']} · "
            f"{step['committed_at']}"
        )
        for step in _items(stream["steps"])
        if _object(step["result"])["intent"] is not None
    }
    checks = {
        str(check["check_id"]): f"{check['recorded_at']} · {check['status']}"
        for check in _items(context["order_checks"])
    }
    self.step_choices, self.check_choices = frozenset(steps), frozenset(checks)
    with ui.card():
        ui.label("固定影子目标的一手历史预算").classes("text-h6")
        ui.label(
            "方向取原目标，固定一手。使用原配置与柜台证据，不输入账户、资金或费率。"
            "预算不刷新事实时间，不创建订单、预占或执行权限。"
        )
        self.budget_step = ui.select(steps, label="本页已保存的影子目标", value=None).props(
            'data-testid="stream-budget-step" options-dense'
        )
        self.budget_check = ui.select(checks, label="同账户固定委托核对", value=None).props(
            'data-testid="stream-budget-check" options-dense'
        )
        self.price = ui.input("一手开仓限价", value="").props(
            'data-testid="stream-budget-price" inputmode="decimal" maxlength="80"'
        )
        button = ui.button("保存一手历史预算（不发单）", on_click=self.budget).props(
            'data-testid="stream-budget-submit"'
        )
        self.actions.append((button, bool(steps and checks)))
        ui.label("候选与选择不随刷新更换；没有候选时，请先查看已有影子结果和账户核对。")
        table = _table(
            "stream-budgets",
            {
                "at": "记录时间",
                "sequence": "来源步骤",
                "status": "历史预算结论",
            },
        )
        table.rows = [
            {
                "id": item["budget_id"],
                "at": item["recorded_at"],
                "sequence": item["sequence"],
                "status": _budget_status(item["status"]),
            }
            for item in _items(context["budgets"])
        ]
        table.selection = "single"
        link = ui.link("查看所选历史预算", "#")
        link.set_visibility(False)

        def select(event: TableSelectionEventArguments) -> None:
            known = {str(row["id"]) for row in table.rows}
            selected = str(event.selection[0]["id"]) if event.selection else ""
            link.set_visibility(selected in known)
            if selected in known:
                link.props["href"] = f"/broker/opening-budgets/{UUID(selected)}"

        table.on_select(select)


def _archive(self: _StreamPage) -> None:
    with ui.card():
        ui.label("固定回调前缀归档与市场段加工").classes("text-h6")
        ui.label(
            "只读取保存的回调 JSON，最多 5 MiB；保留原接收时间，不是网络原文或 CSV。"
            "重新计算分钟，不复制影子结果，不补旧决策。"
        )
        self.archive_upper = ui.input("固定前缀：从序号 1 到", value=str(self.upper)).props(
            'data-testid="stream-archive-through" inputmode="numeric" maxlength="6"'
        )
        self.session_open = ui.input(
            "首分钟起点（UTC）", placeholder="YYYY-MM-DDTHH:MM:00Z", value=""
        ).props('data-testid="stream-archive-open" maxlength="40"')
        self.session_close = ui.input(
            "最后一分钟完成边界（UTC）", placeholder="YYYY-MM-DDTHH:MM:00Z", value=""
        ).props('data-testid="stream-archive-close" maxlength="40"')
        self.download = ui.checkbox("另行允许本机下载含 TD 账户资料的 JSON", value=False).props(
            'data-testid="stream-archive-download"'
        )
        ui.label(
            f"页面上界固定为 {self.upper}；UTC 左闭右开完整分钟范围须明确填写。"
            "不补缺口、不填零。下载独立默认关闭，不应公开或转发账户资料。"
        ).classes("text-caption")
        button = ui.button("本地归档并检查发布条件", on_click=self.archive).props(
            'data-testid="stream-archive-submit"'
        )
        self.actions.append((button, self.upper > 0))
        self.archives = _table(
            "stream-archives",
            {
                "prefix": "固定前缀",
                "range": "UTC 范围",
                "status": "加工状态",
                "product": "发布产品",
                "reason": "原因",
            },
        )
        self.archives.selection = "single"
        self.archives.on_select(self.select_archive)
        with ui.row().classes("q-gutter-md"):
            self.attempt_link = ui.link("查看所选加工尝试", "#")
            self.source_link = ui.link("托管 JSON 来源", "#")
            self.product_link = ui.link("已发布数据", "#")
            for link in (self.attempt_link, self.source_link, self.product_link):
                link.set_visibility(False)


def render(self: _StreamPage, stream: dict[str, object]) -> None:
    self.current = stream
    state, progress = _object(stream["state"]), _object(stream["account_progress"])
    if self.last_control is not None and stream["reason"] == "OPERATOR_" + self.last_control[0]:
        # Acknowledge this command's observed state. A later RESUME (including
        # from another browser) permits a new PAUSE, whereas duplicate clicks do not.
        action, _, key = self.last_control
        self.last_control = action, self.control_phase(), key
    values = {
        "state": stream["status"],
        "connection": stream["connection"],
        "runtime": _object(stream["live_runtime"])["runtime_id"],
        "observed": _object(stream["live_runtime"])["observed_at"],
        "paused": "暂停或未满足推进条件" if stream["paused"] else "已启用，仍逐条检查",
        "reason": stream["reason"],
        "received": stream["received"],
        "shadow-cursor": stream["cursor"],
        "bytes": stream["byte_count"],
        "trading-days": f"{_text(state.get('TD_trading_day'))} / "
        f"{_text(state.get('MD_trading_day'))}",
        "last-received": state.get("last_received_at"),
        "last-market": state.get("last_market_received_at"),
        "market-age": stream["market_age_seconds"],
        "updated": stream["updated_at"],
        "last-pause-reason": state.get("last_pause_reason"),
        "last-pause-at": state.get("last_pause_at"),
        "account-baseline": progress["baseline_id"] or "未绑定",
        "account-cursor": progress["through_sequence"],
        "account-pending": progress["pending"],
        "account-state": progress["status"],
        "account-reason": progress["reason"],
    }
    for key, value in values.items():
        self.labels[key].set_text(_text(value))
    entry_id = progress["entry_id"]
    self.entry_link.set_visibility(entry_id is not None)
    if entry_id is not None:
        self.entry_link.props["href"] = f"/api/broker/position-entries/{UUID(str(entry_id))}"
    quote = state.get("last_market_data")
    self.quote.set_text(
        "尚未收到行情，不以零价替代。"
        if quote is None
        else " · ".join(
            f"{key} {_text(_object(quote).get(key))}"
            for key in ("ActionDay", "UpdateTime", "LastPrice", "Volume")
        )
    )
    self.quote_detail.set_text(_json(quote))
    market = state.get("market")
    self.warmup.set_text(
        "尚无可处理行情或预热已重置。"
        if market is None
        else f"{_object(market)['status']} · {_object(market)['reason']}"
    )
    self.market_detail.set_text(_json(market))
    self.step_records = {str(step["sequence"]): step for step in _items(stream["steps"])[:10]}
    self.steps.rows = [_step_row(step) for step in self.step_records.values()]
    self.archive_records = {str(item["attempt_id"]): item for item in _items(stream["archives"])}
    self.archives.rows = [_archive_row(item) for item in self.archive_records.values()]
    if stream["status"] in {"STOPPED", "FAILED"}:
        self.timer.deactivate()
        self.poll_notice.set_text("接收已终止；仅显示保存事实，不自动重连。")
    self.enabled()


def render_entries(self: _StreamPage, context: dict[str, object]) -> None:
    self.entry_records = {str(item["entry_id"]): item for item in _items(context["entries"])}
    self.entries.rows = [
        {
            "id": item["entry_id"],
            "ordinal": item["ordinal"],
            "status": item["status"],
            "fills": (
                f"{item['new_fill_count']} / {item['duplicate_count']} / {item['fill_count']}"
            ),
            "source": "查询"
            if item.get("source_stream") is None
            else f"保存流 1–{_object(item['source_stream'])['through_sequence']}",
            "at": item["recorded_at"],
        }
        for item in self.entry_records.values()
    ]
