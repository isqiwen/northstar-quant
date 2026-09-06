"""A per-browser continuous-reception page; broker Modules still own every action.

Polling updates observations only. Evidence choices and retained-prefix limits are
fixed when the page opens, and a disconnected page can never execute buffered commands.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID, uuid4

from nicegui import ui
from nicegui.events import TableSelectionEventArguments
from starlette.concurrency import run_in_threadpool

from northstar_quant.broker.budgets import BrokerOpeningBudgets
from northstar_quant.broker.streams import BrokerStreams
from northstar_quant.broker.workspace import BrokerWorkspace


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


async def show(
    stream_id: UUID,
    streams: BrokerStreams,
    broker: BrokerWorkspace,
    budgets: BrokerOpeningBudgets,
    *,
    authorize: Callable[[], None],
) -> None:
    """Render one already-authenticated page; recheck its authority on every operation."""
    page = _StreamPage(stream_id, streams, broker, budgets, authorize)
    authorize()
    stream, budget_context, ledger_context = await run_in_threadpool(page.initial)
    page.build(stream, budget_context, ledger_context)


class _StreamPage:
    def __init__(
        self,
        identifier: UUID,
        streams: BrokerStreams,
        broker: BrokerWorkspace,
        budgets: BrokerOpeningBudgets,
        authorize: Callable[[], None],
    ) -> None:
        self.identifier = identifier
        self.streams, self.broker, self.budgets = streams, broker, budgets
        self.authorize = authorize
        self.current: dict[str, object] = {}
        self.busy = False
        self.reading = False
        self.read_finished = asyncio.Event()
        self.read_finished.set()
        self.healthy = True
        self.invalid = False
        self.connected_once = False
        self.deleted = False
        self.controls: dict[str, ui.button] = {}
        self.actions: list[tuple[ui.button, bool]] = []
        self.labels: dict[str, ui.label] = {}
        self.commands: dict[str, tuple[UUID, dict[str, object] | None]] = {}
        self.last_control: tuple[str, str, str] | None = None
        self.read_generation = 0

    def initial(self) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        stream = self.streams.get(self.identifier)
        request = _object(_object(stream["binding"])["request"])
        self.query_id = UUID(str(request["query_batch_id"]))
        return (
            stream,
            self.budgets.context(self.identifier),
            self.broker.ledger_context(self.query_id),
        )

    def build(
        self,
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
            self.poll_notice = ui.label("每秒只读更新状态，不连接柜台。 ").props(
                'data-testid="stream-poll-status" role="status"'
            )
            with ui.card():
                ui.label("连接与影子推进").classes("text-h6")
                self._fields(
                    {
                        "state": "持久状态",
                        "connection": "本机接收进程",
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
                    }
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
            self._account(ledger_context)
            self._budget(budget_context, stream)
            self._archive()
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
        self.render(stream)

    def _fields(self, fields: dict[str, str]) -> None:
        with ui.element("div").classes("row full-width q-col-gutter-sm"):
            for key, title in fields.items():
                with ui.column().classes("col-12 col-sm-6 col-md-4"):
                    ui.label(title).classes("text-caption text-grey-7")
                    self.labels[key] = (
                        ui.label()
                        .props(f'data-testid="stream-{key}"')
                        .style("overflow-wrap:anywhere;max-width:100%")
                    )

    def _account(self, context: dict[str, object]) -> None:
        with ui.card():
            ui.label("账户回报入账与本机处理进度").classes("text-h6")
            self._fields(
                {
                    "account-baseline": "本流固定账户基准",
                    "account-cursor": "账户处理至来源序号",
                    "account-pending": "尚待处理的已存回调",
                    "account-state": "本机处理状态",
                    "account-reason": "处理原因",
                }
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
            self.render_entries(context)

    def _budget(self, context: dict[str, object], stream: dict[str, object]) -> None:
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

    def _archive(self) -> None:
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

    def render(self, stream: dict[str, object]) -> None:
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
        self.archive_records = {
            str(item["attempt_id"]): item for item in _items(stream["archives"])
        }
        self.archives.rows = [_archive_row(item) for item in self.archive_records.values()]
        if stream["status"] in {"STOPPED", "FAILED"}:
            self.timer.deactivate()
            self.poll_notice.set_text("接收已终止；仅显示保存事实，不自动重连。")
        self.enabled()

    def render_entries(self, context: dict[str, object]) -> None:
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

    def select_step(self, event: TableSelectionEventArguments) -> None:
        key = str(event.selection[0]["id"]) if event.selection else ""
        step = self.step_records.get(key)
        self.step_detail.set_text(_json(step))
        self.step_source.set_visibility(step is not None)
        if step is not None:
            self.step_source.props["href"] = (
                f"/api/streams/{self.identifier}/events?after={int(key) - 1}"
            )

    def select_entry(self, event: TableSelectionEventArguments) -> None:
        key = str(event.selection[0]["id"]) if event.selection else ""
        entry = self.entry_records.get(key)
        self.entry_detail.set_text(_json(entry))
        self.entry_source.set_visibility(entry is not None)
        projection = None if entry is None else _object(entry["position_projection"])
        self.position_status.set_text(
            "尚未选择固定入账记录。"
            if projection is None
            else f"所选入账持仓：{projection['status']} · 同日 SHFE 投机双向数量；"
            "费用、资金与完整账户仍未对账。"
        )
        self.positions.rows = (
            []
            if projection is None
            else [
                {
                    "id": (
                        f"{position['contract_id']}:{position['direction']}:{position['hedge_flag']}"
                    ),
                    "symbol": f"{position['exchange']} · {position['symbol']}",
                    "direction": position["direction"],
                    "today": position["today_lots"],
                    "yesterday": position["yesterday_lots"],
                }
                for position in _items(projection["positions"])
            ]
        )
        if entry is not None:
            self.entry_source.props["href"] = f"/api/broker/position-entries/{UUID(key)}"

    def select_archive(self, event: TableSelectionEventArguments) -> None:
        key = str(event.selection[0]["id"]) if event.selection else ""
        attempt = self.archive_records.get(key)
        self.attempt_link.set_visibility(attempt is not None)
        self.source_link.set_visibility(attempt is not None)
        self.product_link.set_visibility(attempt is not None and attempt["snapshot_id"] is not None)
        if attempt is not None:
            self.attempt_link.props["href"] = f"/attempts/{UUID(key)}"
            self.source_link.props["href"] = f"/sources/{UUID(str(attempt['source_id']))}"
            if attempt["snapshot_id"] is not None:
                self.product_link.props["href"] = f"/datasets/{UUID(str(attempt['snapshot_id']))}"

    def enabled(self) -> None:
        usable = not self.busy and not self.invalid
        active = self.current["status"] in {"STARTING", "RECEIVING"}
        progress = _object(self.current["account_progress"])
        resume = (
            self.healthy
            and self.current["status"] == "RECEIVING"
            and self.current["connection"] == "RECEIVING"
            and bool(self.current["paused"])
            and not _object(self.current["state"]).get("connection_error")
            and (
                progress["status"] == "UNBOUND"
                or (
                    progress["status"] == "READY"
                    and cast(int, progress["through_sequence"])
                    >= cast(int, self.current["received"])
                )
            )
        )
        for action, button in self.controls.items():
            button.set_enabled(usable and (resume if action == "RESUME" else active))
        for button, available in self.actions:
            button.set_enabled(usable and available)

    async def refresh(self, *, histories: bool = False, wait: bool = False) -> None:
        if self.reading and (wait or histories):
            await self.read_finished.wait()
        if self.inactive() or self.reading or (self.busy and not histories):
            return
        self.reading = True
        self.read_finished.clear()
        generation = self.read_generation
        try:
            self.authorize()
            stream = await run_in_threadpool(self.streams.get, self.identifier)
            if histories:
                self.authorize()
                context = await run_in_threadpool(self.broker.ledger_context, self.query_id)
                if not self.inactive() and generation == self.read_generation:
                    self.render_entries(context)
            if not self.inactive() and generation == self.read_generation:
                self.healthy = True
                self.render(stream)
        except Exception:
            self.healthy = False
            self.timer.deactivate()
            if not self.deleted:
                self.poll_notice.set_text(
                    "读取失败：显示的是旧观察，已停止刷新。请重新打开页面核实。"
                )
                self.enabled()
        finally:
            self.reading = False
            self.read_finished.set()

    def connect(self) -> None:
        # A new socket can finish its handshake before the old socket's close
        # callback runs. Never let that ordering revive this document's actions.
        if self.connected_once:
            self.disconnect()
        self.connected_once = True

    def disconnect(self) -> None:
        self.invalid = True
        self.timer.deactivate()
        self.notice.set_text("页面连接已中断，所有操作已失效；请重新打开页面，勿重放离线操作。")
        self.enabled()

    def delete(self) -> None:
        self.deleted = True
        self.invalid = True
        self.timer.cancel()

    def _through(self, value: object) -> int:
        raw = str(value).strip()
        if (
            len(raw) > 6
            or not raw.isascii()
            or not raw.isdecimal()
            or not 1 <= int(raw) <= self.upper
        ):
            raise ValueError(f"来源上界必须是 1 至本页固定上界 {self.upper} 的整数。")
        return int(raw)

    def inactive(self) -> bool:
        # This can change while a backend call awaits, even inside one callback.
        return self.invalid or self.deleted

    async def perform(
        self, key: str, operation: Callable[[UUID], dict[str, object]]
    ) -> dict[str, object] | None:
        if self.inactive() or self.busy:
            return None
        if len(self.commands) >= 128 and key not in self.commands:
            self.disconnect()
            return None
        self.busy = True
        self.read_generation += 1
        self.enabled()
        self.notice.set_text("正在提交固定输入；重复操作沿用同一命令身份。")
        try:
            self.authorize()
            identifier, result = self.commands.setdefault(key, (uuid4(), None))
            if result is None:
                result = await run_in_threadpool(operation, identifier)
                self.commands[key] = (identifier, result)
            if self.inactive():
                return None
            self.notice.set_text("操作已确认；没有发单、撤单或重放旧策略。")
            return result
        except (ValueError, LookupError) as error:
            self.notice.set_text(f"未完成：{str(error)[:1000]}")
        except Exception:
            self.invalid = True
            self.timer.deactivate()
            self.notice.set_text(
                "无法确认命令结果，已禁用全部操作。请重新打开页面检查已保存结果，勿盲目重投。"
            )
        finally:
            self.busy = False
            if not self.deleted:
                self.enabled()
        return None

    async def control(self, action: str) -> None:
        if self.invalid or self.busy or not self.controls[action].enabled:
            return
        phase = self.control_phase()
        if self.last_control is None or self.last_control[:2] != (action, phase):
            self.last_control = action, phase, f"control:{action}:{uuid4()}"
        result = await self.perform(
            self.last_control[2],
            lambda identifier: self.streams.control(self.identifier, action, request_id=identifier),
        )
        if result is not None:
            await self.refresh(wait=True)

    def control_phase(self) -> str:
        # Received/updated_at change on every tick and are not a new control intent.
        return json.dumps(
            [
                self.current["status"],
                self.current["reason"],
                _object(self.current["state"]).get("last_pause_at"),
            ]
        )

    async def catchup(self) -> None:
        try:
            through = self._through(self.account_upper.value)
            baseline = UUID(str(self.baseline))
        except ValueError as error:
            self.notice.set_text(str(error))
            return
        result = await self.perform(
            f"account:{baseline}:{through}",
            lambda _: self.streams.catchup_account(self.identifier, baseline, through),
        )
        if result is not None:
            await self.refresh(histories=True)

    async def budget(self) -> None:
        try:
            sequence, check = str(self.budget_step.value), str(self.budget_check.value)
            if sequence not in self.step_choices or check not in self.check_choices:
                raise ValueError("请选择本页打开时已保存的目标与核对记录。")
            price = str(self.price.value).strip()
            if not price or len(price) > 80:
                raise ValueError("请填写明确十进制限价（最多 80 字符）。")
            amount = Decimal(price)
            if not amount.is_finite() or amount <= 0:
                raise ValueError("限价必须是有限正数。")
        except (ValueError, InvalidOperation) as error:
            self.notice.set_text(
                "限价格式错误。" if isinstance(error, InvalidOperation) else str(error)
            )
            return
        result = await self.perform(
            f"budget:{sequence}:{check}:{price}",
            lambda identifier: self.budgets.create(
                self.identifier,
                int(sequence),
                UUID(check),
                limit_price=amount,
                request_id=identifier,
            ),
        )
        if result is not None:
            ui.navigate.to(f"/broker/opening-budgets/{UUID(str(result['budget_id']))}")

    async def archive(self) -> None:
        try:
            through = self._through(self.archive_upper.value)
            start, end = str(self.session_open.value).strip(), str(self.session_close.value).strip()
            if not start or not end or len(start) > 40 or len(end) > 40:
                raise ValueError("请明确填写完整分钟的 UTC 起止边界（各最多 40 字符）。")
        except ValueError as error:
            self.notice.set_text(str(error))
            return
        download = bool(self.download.value)
        result = await self.perform(
            "archive:" + json.dumps([through, start, end, download]),
            lambda identifier: self.streams.archive(
                self.identifier,
                through_sequence=through,
                session_open=start,
                session_close=end,
                request_id=identifier,
                allow_download=download,
            ),
        )
        if result is not None:
            ui.navigate.to(f"/attempts/{UUID(str(result['attempt_id']))}")


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
