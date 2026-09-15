"""Admit one initial simulated-funds opening on the already owned receiver.

A saved budget is an input reference, not permission. The order transaction
rechecks the bound query, retained activity, monetary ledger and live quote.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import Connection, Engine

from northstar_quant.accounting.journal import snapshot
from northstar_quant.broker.events import ACCOUNT_ACTIVITY_CALLBACKS, BrokerEvent
from northstar_quant.broker.market import ctp_quote_time
from northstar_quant.broker.order_transport import CtpExecution
from northstar_quant.broker.query_window import receiver_query
from northstar_quant.broker.stream_records import read_stream_source, text
from northstar_quant.execution.journal import require_no_execution_exposure
from northstar_quant.execution.orders import (
    AdmissionRejected,
    Offset,
    OrderBudget,
    PendingOrder,
    Side,
)
from northstar_quant.live.commands import Commands
from northstar_quant.live.execution_authority import ExecutionAuthority
from northstar_quant.market_data.sessions import SessionSchedule
from northstar_quant.messaging import Endpoint

if TYPE_CHECKING:
    from northstar_quant.data_management.library import DataLibrary
    from northstar_quant.live.order_control import ReceiverOrders


@dataclass(frozen=True, slots=True)
class OpenOrder:
    budget_id: UUID
    authorization_id: UUID
    request_id: UUID
    expires_at: datetime


OPEN_ORDER: Endpoint[OpenOrder, dict[str, Any]] = Endpoint("live.execution.open", OpenOrder)


class OpeningRejected(AdmissionRejected):
    """A normal admission refusal; no order has been persisted or sent."""


def execute_opening(
    receiver: ReceiverOrders, library: DataLibrary, command: OpenOrder
) -> dict[str, Any]:
    from northstar_quant.accounting.ledger import BrokerLedger
    from northstar_quant.live.opening_budgets import BrokerOpeningBudgets, _hash

    receiver._core()
    receiver.check_owner()
    identity = {"order_id": str(command.request_id), "stream_id": str(receiver.stream_id)}
    channel, session = receiver.channel, receiver.session
    if (
        receiver.closed
        or not receiver.ready
        or session is None
        or channel is None
        or channel.closed
        or channel.failed
        or channel.pending is not None
    ):
        return {**identity, "status": "REJECTED", "reason": "RECEIVER_NOT_READY"}
    saved = Commands(receiver.engine, receiver.runtime_id).get(command.request_id)
    if (
        saved["runtime_id"] != str(receiver.runtime_id)
        or saved["operator"] != "owner"
        or saved["status"] != "RUNNING"
        or saved["path"] != f"/streams/{receiver.stream_id}/opening-orders"
        or saved["input"]
        != {"budget_id": str(command.budget_id), "authorization_id": str(command.authorization_id)}
    ):
        return {**identity, "status": "REJECTED", "reason": "COMMAND_IDENTITY_INVALID"}
    try:
        fixed = BrokerOpeningBudgets(receiver.engine, library).get(command.budget_id)
    except LookupError:
        return {**identity, "status": "REJECTED", "reason": "BUDGET_NOT_FOUND"}
    if fixed["stream_id"] != str(receiver.stream_id):
        return {**identity, "status": "REJECTED", "reason": "BUDGET_STREAM_MISMATCH"}
    if fixed["status"] != "WITHIN_BUDGET" or fixed["account_check"]["status"] != "UNCHANGED":
        return {**identity, "status": "REJECTED", "reason": "OPENING_INPUTS_UNRESOLVED"}
    entry = BrokerLedger(receiver.engine).get(UUID(fixed["entry_id"]))
    decision, budget = fixed["inputs"]["decision"], fixed["budget"]
    intent, quote = decision["result"]["intent"], decision["event"]["data"]
    authority = ExecutionAuthority(receiver.engine, receiver.runtime_id, receiver.check_owner)
    try:
        consent = authority.get(command.authorization_id)
    except LookupError:
        return {**identity, "status": "REJECTED", "reason": "CONSENT_NOT_FOUND"}
    deadline = min(
        command.expires_at,
        datetime.fromisoformat(saved["expires_at"]),
        datetime.fromisoformat(intent["valid_until"]),
        datetime.fromisoformat(consent["request"]["expires_at"]),
    )
    now = datetime.now(UTC)
    if now >= deadline or now < datetime.fromisoformat(intent["generated_at"]):
        return {**identity, "status": "REJECTED", "reason": "TARGET_OR_COMMAND_EXPIRED"}
    side, price = Side(budget["side"]), Decimal(fixed["limit_price"])
    order = PendingOrder(
        str(command.request_id),
        command.budget_id,
        now,
        deadline,
        side,
        Offset.OPEN,
        1,
        Decimal(quote["LowerLimitPrice"]) if side is Side.BUY else price,
        price if side is Side.BUY else Decimal(quote["UpperLimitPrice"]),
        contract_id=UUID(decision["binding"]["contract_id"]),
        budget=OrderBudget(
            Decimal(budget["fee_budget"]),
            Decimal(budget["margin_budget"]),
            Decimal(budget["notional"]),
            Decimal(0),
        ),
    )
    instrument = {
        **fixed["inputs"]["terms"]["instrument"][0],
        "contract_id": str(order.contract_id),
    }

    proof: dict[str, Any] = {}

    def check_account(connection: Connection) -> None:
        require_no_execution_exposure(connection)
        progress = BrokerLedger(receiver.engine).stream_progress(
            receiver.stream_id, transaction=connection
        )
        if (
            progress["status"] != "READY"
            or progress["pending"] != 0
            or progress["baseline_id"] != entry["baseline_id"]
        ):
            raise OpeningRejected("ACCOUNT_RECEIVER_NOT_BOUND_AND_CAUGHT_UP")
        current = receiver_query(connection, receiver.stream_id)
        at = datetime.now(UTC)
        if current is None or _hash(current) != fixed["inputs"]["query_hash"]:
            raise OpeningRejected("RECEIVER_QUERY_CHANGED")
        latest_decision = connection.execute(
            text(
                "SELECT sequence FROM broker_stream_steps WHERE stream_id=:id "
                "AND json_extract(result, '$.bar') IS NOT NULL ORDER BY sequence DESC LIMIT 1"
            ),
            {"id": receiver.stream_id},
        ).scalar_one_or_none()
        if latest_decision != fixed["sequence"]:
            raise OpeningRejected("TARGET_SUPERSEDED")
        resumed = connection.execute(
            text(
                "SELECT committed_at FROM broker_stream_commands WHERE stream_id=:id "
                "AND action='RESUME' ORDER BY committed_at DESC LIMIT 1"
            ),
            {"id": receiver.stream_id},
        ).scalar_one_or_none()
        if resumed is not None and resumed >= datetime.fromisoformat(decision["step_committed_at"]):
            raise OpeningRejected("TARGET_PRECEDES_RESUME")
        observation = current["account_observation"]
        receipts = observation["account_receipts"]
        if (
            current["status"] != "COMPLETE"
            or len(receipts) != 1
            or not 0
            <= (at - datetime.fromisoformat(receipts[0]["received_at"])).total_seconds()
            <= 5
            or at < datetime.fromisoformat(current["finished_at"])
        ):
            raise OpeningRejected("ACCOUNT_OBSERVATION_NOT_CURRENT")
        prefix = entry.get("source_stream")
        if (
            entry["status"] != "READY"
            or entry["monetary_status"] != "POSTED"
            or not prefix
            or prefix["stream_id"] != str(receiver.stream_id)
            or prefix["through_sequence"] >= current["from_sequence"]
        ):
            raise OpeningRejected("FIXED_RECEIVER_LEDGER_REQUIRED")
        view = snapshot(connection, entry["baseline_id"])
        account = view.account
        checkpoint = account.checkpoint()
        if (
            not view.sources
            or view.sources[-1] != (entry["entry_id"], _hash(entry))
            or any(
                checkpoint[key]
                for key in ("fill_count", "fee_count", "cash_flow_count", "settlement_count")
            )
            or any(
                any(account.position(market.contract_id).to_dict().values())
                for market in account.markets
            )
            or account.pending_fee_fill_ids
            or account.cash != Decimal(observation["amounts"]["Balance"])
        ):
            raise OpeningRejected("FIRST_OPENING_ACCOUNT_NOT_RECONCILED")
        source = read_stream_source(connection, receiver.stream_id)
        binding = cast(dict[str, Any], source["binding"])
        if (
            binding["environment"] != "SANDBOX"
            or binding["runtime_id"] != str(receiver.runtime_id)
            or binding["account_id"] != session.account_id
            or binding["profile"]["name"] != session.profile
        ):
            raise OpeningRejected("RECEIVER_SCOPE_MISMATCH")
        rows = connection.execute(
            text(
                "SELECT sequence, event, event_hash FROM broker_stream_events WHERE stream_id=:id "
                "AND sequence>:after ORDER BY sequence"
            ),
            {"id": receiver.stream_id, "after": prefix["through_sequence"]},
        ).mappings()
        last_quote = None
        for row in rows:
            if _hash(row["event"]) != row["event_hash"]:
                raise ValueError("opening account source is damaged")
            event = BrokerEvent.from_dict(row["event"])
            if event.callback in ACCOUNT_ACTIVITY_CALLBACKS:
                raise OpeningRejected("ACCOUNT_ACTIVITY_AFTER_FIXED_ENTRY")
            if event.channel == "MD" and event.callback == "OnRtnDepthMarketData":
                last_quote = event
        if last_quote is None:
            raise OpeningRejected("CURRENT_QUOTE_REQUIRED")
        data = last_quote.data or {}
        schedule = binding["request"].get("schedule")
        market_at = ctp_quote_time(
            data, schedule=None if schedule is None else SessionSchedule.from_dict(schedule)
        )
        if (
            not -1 <= (at - market_at).total_seconds() <= 5
            or not -1 <= (at - datetime.fromisoformat(last_quote.received_at)).total_seconds() <= 5
            or any(
                data.get(key) != quote.get(key)
                for key in (
                    "InstrumentID",
                    "TradingDay",
                    "LastPrice",
                    "UpperLimitPrice",
                    "LowerLimitPrice",
                    "PreSettlementPrice",
                )
            )
        ):
            raise OpeningRejected("MARKET_CHANGED_RECALCULATE_BUDGET")

        proof.update(
            checked_at=at.isoformat(),
            budget_id=str(command.budget_id),
            budget_hash=_hash(fixed),
            entry_id=entry["entry_id"],
            entry_hash=_hash(entry),
            journal_ordinal=view.ordinal,
            journal_hash=view.content_hash,
            query_id=current["query_id"],
            query_hash=_hash(current),
            stream_id=str(receiver.stream_id),
            through_sequence=source["received"],
            quote_sequence=last_quote.sequence,
            quote_hash=_hash(last_quote.to_dict()),
            scope="INITIAL_FLAT_SANDBOX_OPENING",
            account_progress=progress,
        )

    def admit(connection: Connection) -> dict[str, Any]:
        authority.admit(
            connection,
            command.authorization_id,
            receiver.stream_id,
            order,
            check_current_account=check_account,
        )
        return proof

    def check_dispatch() -> None:
        receiver.check_owner()
        with receiver.engine.connect() as connection:
            state = (
                connection.execute(
                    text("SELECT status, paused FROM broker_streams WHERE stream_id=:id"),
                    {"id": receiver.stream_id},
                )
                .mappings()
                .one()
            )
            if state["status"] != "RECEIVING" or state["paused"]:
                raise OpeningRejected("RECEIVER_PAUSED_BEFORE_DISPATCH")
        if (
            datetime.now(UTC) >= deadline
            or authority.get(command.authorization_id)["status"] != "CONSENTED"
        ):
            raise OpeningRejected("CONSENT_EXPIRED_BEFORE_DISPATCH")

    try:
        return CtpExecution(receiver.engine, receiver.runtime_id, session).submit(
            order,
            command.authorization_id,
            instrument,
            price,
            admit=admit,
            send=channel.send,
            check_owner=check_dispatch,
        )
    except AdmissionRejected as error:
        return {**identity, "status": "REJECTED", "reason": str(error)}


def verify_admissions(engine: Engine, library: DataLibrary) -> int:
    """Check retained sending inputs after restore without reevaluating or resending."""
    from northstar_quant.accounting.ledger import BrokerLedger
    from northstar_quant.broker.stream_records import read_stream_event
    from northstar_quant.execution.journal import OrderJournal
    from northstar_quant.live.opening_budgets import BrokerOpeningBudgets, _hash

    budgets = BrokerOpeningBudgets(engine, library)
    journal = OrderJournal(engine, UUID(int=0))
    count, before = 0, None
    while True:
        page = journal.list(before=before)
        for order in page["orders"]:
            with engine.connect() as connection:
                try:
                    binding = CtpExecution.binding(connection, order["order_id"])
                except LookupError:
                    continue
            proof = binding["admission"]
            if proof is None:
                continue  # Low-level execution fixtures have no Live admission.
            if proof.get("scope") == "CONFIRMED_SANDBOX_TODAY_CLOSE":
                from northstar_quant.live.closing_execution import verify_closing

                with engine.connect() as connection:
                    verify_closing(connection, binding)
                count += 1
                continue
            budget = budgets.get(UUID(proof["budget_id"]))
            entry = BrokerLedger(engine).get(UUID(proof["entry_id"]))
            with engine.connect() as connection:
                quote = read_stream_event(
                    connection, UUID(proof["stream_id"]), proof["quote_sequence"]
                )
                account = snapshot(
                    connection, entry["baseline_id"], through_ordinal=proof["journal_ordinal"]
                )
            if (
                proof["scope"] != "INITIAL_FLAT_SANDBOX_OPENING"
                or proof["budget_hash"] != _hash(budget)
                or proof["entry_hash"] != _hash(entry)
                or proof["entry_id"] != budget["entry_id"]
                or proof["query_id"] != budget["query_id"]
                or proof["query_hash"] != budget["inputs"]["query_hash"]
                or proof["stream_id"] != budget["stream_id"]
                or proof["quote_hash"] != _hash(quote.to_dict())
                or not budget["sequence"] <= quote.sequence <= proof["through_sequence"]
                or account.content_hash != proof["journal_hash"]
                or account.sources[-1] != (entry["entry_id"], _hash(entry))
                or binding["request"]["observation_id"] != budget["budget_id"]
                or not datetime.fromisoformat(binding["request"]["submitted_at"])
                <= datetime.fromisoformat(proof["checked_at"])
                < datetime.fromisoformat(binding["request"]["expires_at"])
                or proof["account_progress"]["status"] != "READY"
                or proof["account_progress"]["pending"] != 0
                or proof["account_progress"]["baseline_id"] != entry["baseline_id"]
                or proof["account_progress"]["through_sequence"] != proof["through_sequence"]
            ):
                raise ValueError(
                    "opening admission differs from retained account and market inputs"
                )
            count += 1
        before = page["next_before"]
        if before is None:
            return count
