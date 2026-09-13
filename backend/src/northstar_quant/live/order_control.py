"""Deliver operator cancellations to the existing receiving core, never a new sender.

The HTTP command is durable before enqueue. The bounded in-memory handoff is not
replayed after process loss. OMS persists the native attempt before dispatch and
retains reservations until the counter confirms the order outcome.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError, Future, TimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from queue import Empty, Full, Queue
from threading import get_ident
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Connection, Engine

from northstar_quant.broker.events import BrokerEvent
from northstar_quant.broker.order_channel import OrderChannel
from northstar_quant.broker.order_transport import CtpExecution, CtpSession
from northstar_quant.broker.stream_records import read_stream_source, text
from northstar_quant.execution.journal import CancellationPending
from northstar_quant.live.commands import Commands
from northstar_quant.messaging import Endpoint


@dataclass(frozen=True, slots=True)
class CancelOrder:
    order_id: str
    request_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshAccount:
    request_id: UUID
    expires_at: datetime


REFRESH_ACCOUNT: Endpoint[RefreshAccount, dict[str, Any]] = Endpoint(
    "live.account.refresh", RefreshAccount
)


CANCEL_ORDER: Endpoint[CancelOrder, dict[str, Any]] = Endpoint("live.execution.cancel", CancelOrder)


class ReceiverOrders:
    def __init__(
        self, engine: Engine, runtime_id: UUID, stream_id: UUID, check_owner: Callable[[], None]
    ) -> None:
        self.engine, self.runtime_id, self.stream_id = engine, runtime_id, stream_id
        self.check_owner = check_owner
        self.thread = get_ident()
        self.channel: OrderChannel | None = None
        self.session: CtpSession | None = None
        self.ready = False
        self.closed = False
        self.pending: Queue[tuple[CancelOrder | RefreshAccount, Future[dict[str, Any]]]] = Queue(
            maxsize=1
        )

    def bind(self, channel: OrderChannel) -> None:
        self._core()
        if self.channel is not None:
            raise ValueError("receiver order channel cannot be rebound")
        self.channel = channel

    def _core(self) -> None:
        if self.thread != get_ident():
            raise RuntimeError("order commands must run on their receiving core")

    def observe(self, event: BrokerEvent) -> None:
        self._core()
        if (
            event.error_id
            and event.callback
            not in {
                "OnRspOrderInsert",
                "OnErrRtnOrderInsert",
                "OnRspOrderAction",
                "OnErrRtnOrderAction",
            }
        ) or event.callback in {"OnFrontDisconnected", "OnHeartBeatWarning"}:
            self.ready = False
            self.session = None
        elif event.channel == "TD" and event.callback == "OnRspUserLogin" and not event.error_id:
            # A new login starts a new transport readiness sequence, even when
            # no preceding disconnect callback was delivered.
            self.ready = False
            self.session = None
            with self.engine.connect() as connection:
                binding = cast(
                    dict[str, Any], read_stream_source(connection, self.stream_id)["binding"]
                )
            try:
                self.session = CtpSession.from_login(
                    binding["profile"]["name"],
                    binding["profile"]["broker_id"],
                    binding["account_id"],
                    event,
                )
            except ValueError:
                # Read-only reception can retain a login whose execution identity
                # is incomplete; that receipt cannot enable native dispatch.
                self.session = None
                self.ready = False

        elif (
            event.channel == "MD"
            and event.callback == "OnRspSubMarketData"
            and event.is_last is True
            and not event.error_id
        ):
            # Native reception installs its dispatch loop only after TD queries
            # and the matching MD login/subscription finish.
            with self.engine.connect() as connection:
                binding = cast(
                    dict[str, Any], read_stream_source(connection, self.stream_id)["binding"]
                )
            self.ready = (
                self.session is not None
                and event.request_id == 0
                and (event.data or {}).get("InstrumentID") == binding["instrument"]
            )

    def request(self, order_id: str, request_id: UUID) -> dict[str, Any]:
        return self._request(
            CancelOrder(order_id, request_id, datetime.now(UTC) + timedelta(seconds=3))
        )

    def request_refresh(self, request_id: UUID) -> dict[str, Any]:
        return self._request(RefreshAccount(request_id, datetime.now(UTC) + timedelta(seconds=3)))

    def _request(self, command: CancelOrder | RefreshAccount) -> dict[str, Any]:
        identity = (
            {"order_id": command.order_id}
            if isinstance(command, CancelOrder)
            else {"stream_id": str(self.stream_id), "query_id": str(command.request_id)}
        )
        if self.closed:
            return {"status": "REJECTED", "reason": "RECEIVER_STOPPED", **identity}
        result: Future[dict[str, Any]] = Future()
        try:
            self.pending.put_nowait((command, result))
        except Full:
            return {"status": "REJECTED", "reason": "RECEIVER_BUSY", **identity}
        try:
            return result.result(timeout=4)
        except CancelledError:
            return {"status": "REJECTED", "reason": "RECEIVER_STOPPED", **identity}
        except TimeoutError as error:
            if result.cancel():
                return {"status": "REJECTED", "reason": "COMMAND_EXPIRED", **identity}
            raise ValueError(
                "receiver command outcome is unknown; inspect the saved command"
            ) from error

    def refresh(self, command: RefreshAccount) -> dict[str, Any]:
        self._core()
        self.check_owner()
        identity = {"stream_id": str(self.stream_id), "query_id": str(command.request_id)}
        if (
            self.closed
            or not self.ready
            or self.session is None
            or self.channel is None
            or self.channel.closed
            or self.channel.failed
        ):
            return {"status": "REJECTED", "reason": "RECEIVER_NOT_READY", **identity}
        saved = Commands(self.engine, self.runtime_id).get(command.request_id)
        if (
            saved["runtime_id"] != str(self.runtime_id)
            or saved["operator"] != "owner"
            or saved["status"] != "RUNNING"
            or saved["path"] != f"/streams/{self.stream_id}/refresh-account"
            or saved["input"] != {}
        ):
            return {"status": "REJECTED", "reason": "COMMAND_IDENTITY_INVALID", **identity}
        deadline = min(command.expires_at, datetime.fromisoformat(saved["expires_at"]))
        if datetime.now(UTC) >= deadline:
            return {"status": "REJECTED", "reason": "COMMAND_EXPIRED", **identity}
        with self.engine.connect() as connection:
            source = read_stream_source(connection, self.stream_id)
            binding = cast(dict[str, Any], source["binding"])
            status = connection.execute(
                text("SELECT status FROM broker_streams WHERE stream_id=:id"),
                {"id": self.stream_id},
            ).scalar_one()
            if (
                status != "RECEIVING"
                or binding["runtime_id"] != str(self.runtime_id)
                or binding["environment"] != "SANDBOX"
                or binding["account_id"] != self.session.account_id
            ):
                return {"status": "REJECTED", "reason": "RECEIVER_SCOPE_MISMATCH", **identity}
        queued = self.channel.refresh_account(command.request_id, deadline)
        return {
            **identity,
            "status": "REQUESTED" if queued else "REJECTED",
            "reason": "AWAITING_RECEIVER_QUERY" if queued else "RECEIVER_BUSY",
        }

    def poll(self, execute: Callable[[CancelOrder | RefreshAccount], dict[str, Any]]) -> None:
        self._core()
        try:
            command, result = self.pending.get_nowait()
        except Empty:
            return
        if not result.set_running_or_notify_cancel():
            return
        try:
            value = execute(command)
        except Exception as error:
            result.set_exception(error)
            raise
        else:
            result.set_result(value)

    def cancel(self, command: CancelOrder) -> dict[str, Any]:
        self._core()
        # Ordinary unavailability rejects this command; it need not fault reception.
        if (
            self.closed
            or not self.ready
            or self.session is None
            or self.channel is None
            or self.channel.closed
            or self.channel.failed
            or self.channel.pending is not None
        ):
            return {
                "status": "REJECTED",
                "reason": "RECEIVER_NOT_READY",
                "order_id": command.order_id,
            }
        if datetime.now(UTC) >= command.expires_at:
            return {"status": "REJECTED", "reason": "COMMAND_EXPIRED", "order_id": command.order_id}
        saved = Commands(self.engine, self.runtime_id).get(command.request_id)
        if (
            saved["runtime_id"] != str(self.runtime_id)
            or saved["operator"] != "owner"
            or saved["status"] != "RUNNING"
            or saved["path"] != f"/execution/orders/{command.order_id}/cancel"
            or saved["input"] != {"stream_id": str(self.stream_id)}
            or datetime.now(UTC) >= datetime.fromisoformat(saved["expires_at"])
        ):
            return {
                "status": "REJECTED",
                "reason": "COMMAND_IDENTITY_INVALID",
                "order_id": command.order_id,
            }
        session = self.session
        with self.engine.connect() as connection:
            try:
                original = CtpExecution.binding(connection, command.order_id)
            except LookupError:
                return {
                    "status": "REJECTED",
                    "reason": "ORDER_NOT_OWNED",
                    "order_id": command.order_id,
                }
            source = read_stream_source(connection, self.stream_id)
            binding = cast(dict[str, Any], source["binding"])
            expected = session.to_dict()
            if (
                any(
                    original["session"][key] != expected[key]
                    for key in ("profile", "broker_id", "account_id", "trading_day")
                )
                or original["instrument"]["contract_id"] != binding["contract_id"]
            ):
                return {
                    "status": "REJECTED",
                    "reason": "ORDER_SCOPE_MISMATCH",
                    "order_id": command.order_id,
                }

        def admit(connection: Connection) -> None:
            self.check_owner()
            source = read_stream_source(connection, self.stream_id)
            binding = cast(dict[str, Any], source["binding"])
            row = (
                connection.execute(
                    text("SELECT status FROM broker_streams WHERE stream_id=:id"),
                    {"id": self.stream_id},
                )
                .mappings()
                .one()
            )
            if (
                row["status"] != "RECEIVING"
                or binding["runtime_id"] != str(self.runtime_id)
                or binding["environment"] != "SANDBOX"
            ):
                raise ValueError("cancellation requires this owned receiving sandbox")
            if datetime.now(UTC) >= min(
                command.expires_at, datetime.fromisoformat(saved["expires_at"])
            ):
                raise ValueError("cancellation expired before durable dispatch")
            # CtpExecution separately verifies profile/account against the original
            # immutable native binding, and retains the original front/session/ref.
            original = CtpExecution.binding(connection, command.order_id)
            if (
                original["session"]["trading_day"] != session.trading_day.isoformat()
                or original["instrument"]["contract_id"] != binding["contract_id"]
            ):
                raise ValueError("cancellation differs from the receiver day or contract")

        def check_dispatch() -> None:
            self.check_owner()
            if datetime.now(UTC) >= min(
                command.expires_at, datetime.fromisoformat(saved["expires_at"])
            ):
                raise ValueError("cancellation expired before native transport")

        channel = self.channel

        def send_cancel(
            method: str, fields: dict[str, Any], request_id: int, deadline: datetime
        ) -> int:
            return channel.send(
                method,
                fields,
                request_id,
                min(deadline, command.expires_at, datetime.fromisoformat(saved["expires_at"])),
            )

        try:
            result = CtpExecution(self.engine, self.runtime_id, session).cancel(
                command.order_id,
                command.request_id,
                admit=admit,
                send=send_cancel,
                check_owner=check_dispatch,
            )
        except CancellationPending:
            return {
                "status": "REJECTED",
                "reason": "CANCELLATION_UNRESOLVED",
                "order_id": command.order_id,
            }
        no_action = (
            result["status"] in {"FILLED", "CANCELED", "REJECTED"}
            or result["filled_lots"] == result["quantity_lots"]
        )
        return {
            "status": "NOT_NEEDED" if no_action else "ATTEMPT_RECORDED",
            "order_id": command.order_id,
            "order": result,
        }

    def close(self) -> None:
        self._core()
        self.closed, self.ready = True, False
        try:
            _, result = self.pending.get_nowait()
        except Empty:
            return
        result.cancel()
