"""Explicit one-lot closing through the same receiver and durable CTP sender.

Only a confirmed first opening in this session is covered. Inventory and a fresh
full-account query must agree. Pending opening fees remain unknown and reserved;
a reduction neither invents their amount nor claims complete cash reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import Connection

from northstar_quant.accounting.journal import snapshot
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.accounting.terms import ChargeRate
from northstar_quant.broker.account_reports import decode_trade, position_observations
from northstar_quant.broker.events import ACCOUNT_ACTIVITY_CALLBACKS, BrokerEvent
from northstar_quant.broker.execution_fills import confirmed_fill
from northstar_quant.broker.market import ctp_quote_time
from northstar_quant.broker.order_transport import CtpExecution
from northstar_quant.broker.query_window import receiver_query
from northstar_quant.broker.stream_records import read_stream_source, text
from northstar_quant.data_management.broker import verify_broker_contract
from northstar_quant.execution.journal import OrderJournal
from northstar_quant.execution.orders import AdmissionRejected, Offset, PendingOrder, Side
from northstar_quant.live.commands import Commands
from northstar_quant.live.execution_authority import ExecutionAuthority
from northstar_quant.live.opening_inputs import _amount, _at, _one
from northstar_quant.market_data.sessions import SessionSchedule
from northstar_quant.messaging import Endpoint
from northstar_quant.risk.closing_budget import closing_budget

if TYPE_CHECKING:
    from northstar_quant.live.order_control import ReceiverOrders


@dataclass(frozen=True, slots=True)
class CloseOrder:
    opening_order_id: UUID
    query_id: UUID
    authorization_id: UUID
    limit_price: Decimal
    request_id: UUID
    expires_at: datetime


CLOSE_ORDER: Endpoint[CloseOrder, dict[str, Any]] = Endpoint("live.execution.close", CloseOrder)


def _section(query: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return _one(query, name)
    except ValueError as error:
        raise AdmissionRejected(str(error)) from error


def execute_closing(receiver: ReceiverOrders, command: CloseOrder) -> dict[str, Any]:
    from northstar_quant.live.opening_budgets import _hash

    receiver._core()
    receiver.check_owner()
    identity = {"order_id": str(command.request_id), "stream_id": str(receiver.stream_id)}
    session, channel = receiver.session, receiver.channel
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
    expected = dict(
        opening_order_id=str(command.opening_order_id),
        query_id=str(command.query_id),
        authorization_id=str(command.authorization_id),
        limit_price=str(command.limit_price),
    )
    if (
        saved["operator"] != "owner"
        or saved["status"] != "RUNNING"
        or saved["runtime_id"] != str(receiver.runtime_id)
        or saved["path"] != f"/streams/{receiver.stream_id}/closing-orders"
        or saved["input"] != expected
    ):
        return {**identity, "status": "REJECTED", "reason": "COMMAND_IDENTITY_INVALID"}
    authority = ExecutionAuthority(receiver.engine, receiver.runtime_id, receiver.check_owner)
    try:
        original = OrderJournal(receiver.engine, receiver.runtime_id).get(
            str(command.opening_order_id)
        )
        consent = authority.get(command.authorization_id)
        with receiver.engine.connect() as connection:
            opening = CtpExecution.binding(connection, str(command.opening_order_id))
            query = receiver_query(connection, receiver.stream_id, query_id=command.query_id)
    except LookupError:
        return {**identity, "status": "REJECTED", "reason": "CLOSING_SOURCE_NOT_FOUND"}
    admitted = opening["admission"]
    if (
        original["status"] != "FILLED"
        or original["filled_lots"] != 1
        or original["quantity_lots"] != 1
        or admitted is None
        or admitted["scope"] != "INITIAL_FLAT_SANDBOX_OPENING"
        or admitted["stream_id"] != str(receiver.stream_id)
        or opening["session"] != session.to_dict()
        or query is None
    ):
        return {**identity, "status": "REJECTED", "reason": "CONFIRMED_SESSION_OPENING_REQUIRED"}
    now = datetime.now(UTC)
    deadline = min(
        command.expires_at, _at(saved["expires_at"]), _at(consent["request"]["expires_at"])
    )
    if now >= deadline:
        return {**identity, "status": "REJECTED", "reason": "COMMAND_OR_CONSENT_EXPIRED"}
    side = Side.SELL if opening["request"]["side"] == "BUY" else Side.BUY
    contract_id = UUID(opening["request"]["contract_id"])
    proof: dict[str, Any] = {}
    instrument: dict[str, Any] = {}
    candidate: PendingOrder | None = None

    def account_inputs(connection: Connection) -> PendingOrder:
        nonlocal instrument
        source = read_stream_source(connection, receiver.stream_id)
        binding = cast(dict[str, Any], source["binding"])
        current = receiver_query(connection, receiver.stream_id)
        if current is None or _hash(current) != _hash(query) or current["status"] != "COMPLETE":
            raise AdmissionRejected("COMPLETE_CURRENT_QUERY_REQUIRED")
        if (
            binding["environment"] != "SANDBOX"
            or binding["runtime_id"] != str(receiver.runtime_id)
            or binding["account_id"] != session.account_id
            or binding["profile"]["name"] != session.profile
            or current["completeness"]["trading_day"] != session.trading_day.strftime("%Y%m%d")
        ):
            raise AdmissionRejected("CLOSING_SESSION_SCOPE_MISMATCH")
        progress = BrokerLedger(receiver.engine).stream_progress(
            receiver.stream_id, transaction=connection
        )
        if (
            progress["status"] != "READY"
            or progress["pending"] != 0
            or progress["baseline_id"] != admitted["account_progress"]["baseline_id"]
        ):
            raise AdmissionRejected("ACCOUNT_RECEIVER_NOT_BOUND_AND_CAUGHT_UP")
        view = snapshot(connection, progress["baseline_id"])
        facts = view.account.applied_fills
        if len(facts) != 1 or facts[0].fact.contract_id != contract_id:
            raise AdmissionRejected("FIRST_ROUND_POSITION_SCOPE_REQUIRED")
        fact = facts[0].fact
        confirmed = confirmed_fill(connection, fact.fill_id)
        if confirmed.order_id != str(command.opening_order_id) or confirmed.quantity_lots != 1:
            raise AdmissionRejected("OPENING_FILL_ASSOCIATION_REQUIRED")
        position = view.account.position(contract_id)
        expected_position = dict(
            long_today=1 if side is Side.SELL else 0,
            short_today=1 if side is Side.BUY else 0,
            long_yesterday=0,
            short_yesterday=0,
        )
        if position.to_dict() != expected_position:
            raise AdmissionRejected("CONFIRMED_TODAY_POSITION_REQUIRED")
        observed, complete, problems = position_observations(current)
        key = ("SHFE", binding["instrument"].upper(), "1", "LONG" if side is Side.SELL else "SHORT")
        nonempty = {key: value for key, value in observed.items() if any(value.values())}
        if not complete or problems or nonempty != {key: {"today": 1, "yesterday": 0}}:
            raise AdmissionRejected("CURRENT_BROKER_POSITION_DIFFERS")
        trade = decode_trade(_section(current, "trades"), binding)
        if (
            trade["fill_id"] != fact.fill_id
            or trade["quantity_lots"] != 1
            or Decimal(trade["price"]) != fact.price
            or trade["offset"] != "OPEN"
            or trade["direction"] != confirmed.side.value
            or trade["symbol"] != binding["instrument"].upper()
        ):
            raise AdmissionRejected("CURRENT_BROKER_TRADES_DIFFER")
        reported = _section(current, "orders")
        if (
            reported.get("OrderRef", "").strip() != opening["fields"]["OrderRef"]
            or reported.get("OrderStatus") != "0"
            or reported.get("VolumeTraded") != 1
            or reported.get("VolumeTotal") != 0
            or reported.get("OrderSysID", "").strip() != trade["order_sys_id"].strip()
        ):
            raise AdmissionRejected("CURRENT_BROKER_ORDERS_DIFFER")
        observation = current["account_observation"]
        receipts = observation["account_receipts"]
        at = datetime.now(UTC)
        if (
            len(receipts) != 1
            or not 0 <= (at - _at(receipts[0]["received_at"])).total_seconds() <= 5
            or _at(current["finished_at"]) > at
            or observation["account_activity_during_query"]
        ):
            raise AdmissionRejected("ACCOUNT_OBSERVATION_NOT_CURRENT")
        funds = _section(current, "account")
        if (
            funds.get("BrokerID") != session.broker_id
            or funds.get("AccountID") != session.account_id
            or funds.get("CurrencyID") != "CNY"
            or funds.get("BizType") != "1"
            or funds.get("TradingDay") != session.trading_day.strftime("%Y%m%d")
            or any(
                _amount(funds.get(name)) != 0
                for name in ("FrozenMargin", "FrozenCash", "FrozenCommission")
            )
        ):
            raise AdmissionRejected("CLOSING_ACCOUNT_SCOPE_OR_FREEZES_UNRESOLVED")
        quote: BrokerEvent | None = None
        for row in connection.execute(
            text(
                "SELECT event, event_hash FROM broker_stream_events WHERE stream_id=:id "
                "AND sequence>:after ORDER BY sequence"
            ),
            {"id": receiver.stream_id, "after": current["from_sequence"]},
        ).mappings():
            if _hash(row["event"]) != row["event_hash"]:
                raise ValueError("closing receipt integrity differs")
            event = BrokerEvent.from_dict(row["event"])
            if event.callback in ACCOUNT_ACTIVITY_CALLBACKS:
                raise AdmissionRejected("ACCOUNT_ACTIVITY_AFTER_QUERY_STARTED")
            if event.channel == "MD" and event.callback == "OnRtnDepthMarketData":
                quote = event
        if quote is None:
            raise AdmissionRejected("FRESH_CLOSING_MARKET_REQUIRED")
        market = quote.data or {}
        market_at = ctp_quote_time(
            market,
            schedule=SessionSchedule.from_dict(binding["request"]["schedule"])
            if "schedule" in binding["request"]
            else None,
        )
        if (
            market_at is None
            or not -1 <= (at - market_at).total_seconds() <= 5
            or not -1 <= (at - _at(quote.received_at)).total_seconds() <= 5
            or market.get("InstrumentID") != binding["instrument"]
            or market.get("TradingDay") != session.trading_day.strftime("%Y%m%d")
        ):
            raise AdmissionRejected("FRESH_CLOSING_MARKET_REQUIRED")
        instrument = {**_section(current, "instrument"), "contract_id": str(contract_id)}
        verify_broker_contract(receiver.engine, contract_id, instrument)
        fee = _section(current, "commission")
        if (
            instrument.get("IsTrading") != 1
            or instrument.get("MinLimitOrderVolume") != 1
            or instrument.get("MaxLimitOrderVolume", 0) < 1
            or any(
                fee.get(name) != value
                for name, value in dict(
                    BrokerID=session.broker_id,
                    InvestorID=session.account_id,
                    InstrumentID=binding["instrument"],
                    ExchangeID="SHFE",
                    InvestorRange="3",
                    InvestUnitID="",
                    BizType="1",
                ).items()
            )
        ):
            raise AdmissionRejected("CURRENT_CLOSING_TERMS_REQUIRED")
        try:
            budget = closing_budget(
                side=side,
                limit_price=command.limit_price,
                last_price=_amount(market.get("LastPrice")),
                price_tick=_amount(instrument.get("PriceTick")),
                multiplier=Decimal(instrument["VolumeMultiple"]),
                lower_limit=_amount(market.get("LowerLimitPrice")),
                upper_limit=_amount(market.get("UpperLimitPrice")),
                fee=ChargeRate(
                    _amount(fee.get("CloseTodayRatioByMoney")),
                    _amount(fee.get("CloseTodayRatioByVolume")),
                ),
                max_adverse_fraction=_amount(
                    str(
                        binding["configuration"]["config"]["risk"][
                            "max_adverse_price_move_fraction"
                        ]
                    )
                ),
            )
        except ValueError as error:
            raise AdmissionRejected(str(error)) from error

        if _amount(funds.get("Available")) < budget.fee:
            raise AdmissionRejected("CURRENT_AVAILABLE_FUNDS_DO_NOT_COVER_CLOSE_FEE")
        result = PendingOrder(
            str(command.request_id),
            command.query_id,
            now,
            deadline,
            side,
            Offset.CLOSE_TODAY,
            1,
            command.limit_price if side is Side.SELL else _amount(market.get("LowerLimitPrice")),
            command.limit_price if side is Side.BUY else _amount(market.get("UpperLimitPrice")),
            contract_id=contract_id,
            budget=budget,
        )
        proof.update(
            scope="CONFIRMED_SANDBOX_TODAY_CLOSE",
            checked_at=at.isoformat(),
            opening_order_id=str(command.opening_order_id),
            opening_hash=_hash(opening),
            stream_id=str(receiver.stream_id),
            query_id=str(command.query_id),
            query_hash=_hash(current),
            journal_ordinal=view.ordinal,
            journal_hash=view.content_hash,
            account_progress=progress,
            quote_sequence=quote.sequence,
            quote_hash=_hash(quote.to_dict()),
            through_sequence=source["received"],
            instrument=instrument,
            fee_terms=fee,
        )
        return result

    try:
        with receiver.engine.connect() as connection:
            candidate = account_inputs(connection)

        def admit(connection: Connection) -> dict[str, Any]:
            def check(connection: Connection) -> None:
                if account_inputs(connection) != candidate:
                    raise AdmissionRejected("CLOSING_INPUTS_CHANGED")

            authority.admit(
                connection,
                command.authorization_id,
                receiver.stream_id,
                candidate,
                check_current_account=check,
            )
            return proof

        def dispatch() -> None:
            receiver.check_owner()
            with receiver.engine.connect() as connection:
                state = (
                    connection.execute(
                        text(
                            "SELECT status, paused, reason, state FROM broker_streams "
                            "WHERE stream_id=:id"
                        ),
                        {"id": receiver.stream_id},
                    )
                    .mappings()
                    .one()
                )
                if state["state"].get("connection_error"):
                    raise AdmissionRejected("RECEIVER_FAULTED_BEFORE_DISPATCH")
                if state["status"] != "RECEIVING" or (
                    state["paused"] and state["reason"] != "OPERATOR_PAUSE"
                ):
                    raise AdmissionRejected("RECEIVER_STOPPED_BEFORE_DISPATCH")
            if (
                datetime.now(UTC) >= deadline
                or authority.get(command.authorization_id)["status"] != "CONSENTED"
            ):
                raise AdmissionRejected("CONSENT_EXPIRED_BEFORE_DISPATCH")

        return CtpExecution(receiver.engine, receiver.runtime_id, session).submit(
            candidate,
            command.authorization_id,
            instrument,
            command.limit_price,
            admit=admit,
            send=channel.send,
            check_owner=dispatch,
        )
    except (AdmissionRejected, LookupError) as error:
        return {**identity, "status": "REJECTED", "reason": str(error)}


def verify_closing(connection: Connection, binding: dict[str, Any]) -> None:
    """Restore fixed reduction evidence without new observations or dispatch."""
    from northstar_quant.broker.stream_records import read_stream_event
    from northstar_quant.live.opening_budgets import _hash

    proof = binding["admission"]
    original = CtpExecution.binding(connection, proof["opening_order_id"])
    query = receiver_query(connection, UUID(proof["stream_id"]), query_id=UUID(proof["query_id"]))
    quote = read_stream_event(connection, UUID(proof["stream_id"]), proof["quote_sequence"])
    progress = proof["account_progress"]
    account = snapshot(
        connection, progress["baseline_id"], through_ordinal=proof["journal_ordinal"]
    )
    order = PendingOrder.from_dict(binding["request"])
    fills = account.account.applied_fills
    if (
        proof["scope"] != "CONFIRMED_SANDBOX_TODAY_CLOSE"
        or original["admission"]["scope"] != "INITIAL_FLAT_SANDBOX_OPENING"
        or original["admission"]["stream_id"] != proof["stream_id"]
        or original["session"] != binding["session"]
        or _hash(original) != proof["opening_hash"]
        or query is None
        or _hash(query) != proof["query_hash"]
        or _hash(quote.to_dict()) != proof["quote_hash"]
        or not query["from_sequence"] < quote.sequence <= proof["through_sequence"]
        or account.content_hash != proof["journal_hash"]
        or progress["status"] != "READY"
        or progress["pending"] != 0
        or progress["through_sequence"] != proof["through_sequence"]
        or proof["instrument"] != binding["instrument"]
        or proof["fee_terms"] != _one(query, "commission")
        or len(fills) != 1
        or fills[0].fact.quantity_lots != 1
        or confirmed_fill(connection, fills[0].fact.fill_id).order_id != proof["opening_order_id"]
        or order.offset is not Offset.CLOSE_TODAY
        or order.quantity_lots != 1
        or order.side.value == original["request"]["side"]
        or order.contract_id != fills[0].fact.contract_id
        or str(order.observation_id) != proof["query_id"]
        or not order.submitted_at <= _at(proof["checked_at"]) < order.expires_at
    ):
        raise ValueError("closing admission differs from its retained execution/account inputs")
