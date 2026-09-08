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

from northstar_quant.apps.live.stream import view
from northstar_quant.apps.live.stream.view import _items, _json, _object
from northstar_quant.live import CommandUnknown, LiveClient, RuntimeUnavailable


async def show(
    stream_id: UUID,
    live: LiveClient,
    *,
    authorize: Callable[[], None],
) -> None:
    """Render one already-authenticated page; recheck its authority on every operation."""
    page = _StreamPage(stream_id, live, authorize)
    authorize()
    stream, budget_context, ledger_context = await run_in_threadpool(page.initial)
    view.build(page, stream, budget_context, ledger_context)


class _StreamPage:
    # View construction owns widgets; this controller owns their fixed inputs,
    # observed facts and command/session state.
    upper: int
    baseline: object
    timer: ui.timer
    notice: ui.label
    poll_notice: ui.label
    command_lookup: ui.link
    steps: ui.table
    step_source: ui.link
    step_detail: ui.label
    quote: ui.label
    quote_detail: ui.label
    warmup: ui.label
    market_detail: ui.label
    entry_link: ui.link
    account_upper: ui.input
    entries: ui.table
    entry_source: ui.link
    position_status: ui.label
    positions: ui.table
    entry_detail: ui.label
    step_choices: frozenset[str]
    check_choices: frozenset[str]
    budget_step: ui.select
    budget_check: ui.select
    price: ui.input
    archive_upper: ui.input
    session_open: ui.input
    session_close: ui.input
    download: ui.checkbox
    archives: ui.table
    attempt_link: ui.link
    source_link: ui.link
    product_link: ui.link
    step_records: dict[str, dict[str, object]]
    archive_records: dict[str, dict[str, object]]
    entry_records: dict[str, dict[str, object]]

    def __init__(
        self,
        identifier: UUID,
        live: LiveClient,
        authorize: Callable[[], None],
    ) -> None:
        self.identifier = identifier
        self.live = live
        self.streams, self.broker, self.budgets = live.streams, live.broker, live.opening_budgets
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
        self.runtime_id = UUID(str(_object(stream["live_runtime"])["runtime_id"]))
        self.live = self.live.for_runtime(self.runtime_id)
        self.streams = self.live.streams
        self.broker = self.live.broker
        self.budgets = self.live.opening_budgets
        request = _object(_object(stream["binding"])["request"])
        self.query_id = UUID(str(request["query_batch_id"]))
        return (
            stream,
            self.budgets.context(self.identifier),
            self.broker.ledger_context(self.query_id),
        )

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
        usable = not self.busy and not self.invalid and self.healthy
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
            if UUID(str(_object(stream["live_runtime"])["runtime_id"])) != self.runtime_id:
                self.invalid = True
                self.timer.deactivate()
                self.notice.set_text("Live 运行身份已经改变，旧页面操作已失效。请重新打开核查。")
                self.enabled()
                return
            if histories:
                self.authorize()
                context = await run_in_threadpool(self.broker.ledger_context, self.query_id)
                if not self.inactive() and generation == self.read_generation:
                    view.render_entries(self, context)
            if not self.inactive() and generation == self.read_generation:
                self.healthy = True
                view.render(self, stream)
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
        if self.inactive() or self.busy or not self.healthy:
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
        except CommandUnknown as error:
            self.invalid = True
            self.timer.deactivate()
            self.notice.set_text(
                f"命令 {error.request_id} 结果未知，全部操作已禁用。只查询原身份，勿重发。"
            )
            self.command_lookup.props["href"] = f"/live/commands/{error.request_id}"
            self.command_lookup.set_visibility(True)
        except RuntimeUnavailable:
            self.healthy = False
            self.timer.deactivate()
            self.notice.set_text("Live 不可用，未能提交操作；请重新打开页面核实运行身份与状态。")
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
            lambda identifier: self.streams.catchup_account(
                self.identifier, baseline, through, request_id=identifier
            ),
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
