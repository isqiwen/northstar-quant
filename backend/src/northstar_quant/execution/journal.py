"""Durable order admission and one-attempt dispatch, owned by a local execution session.

Risk/Live admission and account posting receive the same writer transaction.
The transport runs only after commit. An SDK return is not a broker acceptance;
reopening this journal cannot repeat an attempt or release an unresolved budget.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Connection, Engine, Integer, MetaData, String, Table, select

from northstar_quant.accounting.fills import FillFact
from northstar_quant.persistence.sql import write_transaction

from .orders import PendingOrder, reservation

_metadata = MetaData()
_orders = Table(
    "execution_orders",
    _metadata,
    Column("order_id", String, primary_key=True),
    Column("contract_id", String, nullable=False, index=True),
    Column("authorization_id", String, nullable=False),
    Column("runtime_id", String, nullable=False),
    Column("attempt_id", String, nullable=False, unique=True),
    Column("request", JSON, nullable=False),
    Column("filled_lots", Integer, nullable=False),
    Column("reported_lots", Integer),
    Column("broker_state", String),
    Column("conflicted", Integer, nullable=False),
    Column("status", String, nullable=False),
)
_events = Table(
    "execution_order_events",
    _metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String, nullable=False, unique=True),
    Column("order_id", String, nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("document", JSON, nullable=False),
)
_TERMINAL = {"FILLED", "CANCELED", "REJECTED"}
_REPORTS = {"UNKNOWN", "ACCEPTED", "PARTIALLY_FILLED", *_TERMINAL}


def initialize_journal(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        raise ValueError("external execution journal requires local SQLite")
    _metadata.create_all(connection)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS execution_order_identity BEFORE UPDATE ON execution_orders
        WHEN OLD.order_id IS NOT NEW.order_id OR OLD.contract_id IS NOT NEW.contract_id
          OR OLD.authorization_id IS NOT NEW.authorization_id
          OR OLD.runtime_id IS NOT NEW.runtime_id OR OLD.attempt_id IS NOT NEW.attempt_id
          OR OLD.request IS NOT NEW.request OR NEW.filled_lots < OLD.filled_lots
          OR NEW.conflicted < OLD.conflicted
        BEGIN SELECT RAISE(ABORT, 'Execution identity is immutable'); END
    """)
    for table, actions in (
        ("execution_orders", ("DELETE",)),
        ("execution_order_events", ("UPDATE", "DELETE")),
    ):
        for action in actions:
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS retain_{table}_{action} "
                f"BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Execution facts are immutable'); END"
            )


def _record(
    connection: Connection, event_id: str, order_id: str, kind: str, document: dict[str, Any]
) -> bool:
    existing = (
        connection.execute(select(_events).where(_events.c.event_id == event_id))
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        if (existing["order_id"], existing["kind"], existing["document"]) != (
            order_id,
            kind,
            document,
        ):
            raise ValueError("execution fact identity is bound to different input")
        return False
    connection.execute(
        _events.insert().values(
            event_id=event_id,
            order_id=order_id,
            kind=kind,
            recorded_at=datetime.now(UTC).isoformat(),
            document=document,
        )
    )
    return True


def _get(connection: Connection, order_id: str) -> dict[str, Any]:
    row = (
        connection.execute(select(_orders).where(_orders.c.order_id == order_id))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("local execution order not found")
    return dict(row)


def _status(row: dict[str, Any]) -> str:
    if row["conflicted"] or row["broker_state"] == "UNKNOWN":
        return "UNKNOWN"
    quantity = PendingOrder.from_dict(row["request"]).quantity_lots
    accepted, reported, state = row["filled_lots"], row["reported_lots"], row["broker_state"]
    # A terminal cumulative report cannot stand in for missing individual fills.
    if reported is not None and reported != accepted:
        return "UNKNOWN"
    if accepted == quantity:
        return "FILLED"
    if state in _TERMINAL:
        return str(state)
    return "PARTIALLY_FILLED" if accepted else "ACCEPTED" if state else "UNKNOWN"


def _apply_report(row: dict[str, Any], state: str, cumulative_lots: int) -> None:
    if state not in _REPORTS or type(cumulative_lots) is not int:
        raise ValueError("unsupported broker report")
    quantity = PendingOrder.from_dict(row["request"]).quantity_lots
    if not 0 <= cumulative_lots <= quantity or (
        state == "FILLED"
        and cumulative_lots != quantity
        or state == "REJECTED"
        and cumulative_lots != 0
        or state == "PARTIALLY_FILLED"
        and not 0 < cumulative_lots < quantity
        or state == "ACCEPTED"
        and cumulative_lots != 0
    ):
        raise ValueError("broker status conflicts with cumulative quantity")
    previous = row["reported_lots"]
    if (previous is not None and cumulative_lots < previous) or (
        row["broker_state"] in _TERMINAL
        and (row["broker_state"], previous) != (state, cumulative_lots)
    ):
        row["conflicted"] = 1
    row.update(reported_lots=cumulative_lots, broker_state=state)
    row["status"] = _status(row)


def _apply_fill(row: dict[str, Any], fact: FillFact) -> None:
    order = PendingOrder.from_dict(row["request"])
    if (fact.contract_id, fact.side, fact.offset) != (
        order.contract_id,
        order.side,
        order.offset,
    ):
        raise ValueError("fill does not belong to the fixed local order")
    if row["filled_lots"] + fact.quantity_lots > order.quantity_lots:
        raise ValueError("individual fills exceed the local order quantity")
    row["filled_lots"] += fact.quantity_lots
    # A later fill contradicting an already matched terminal report must
    # be accounted for, but it cannot silently re-arm this contract.
    if row["status"] in {"CANCELED", "REJECTED"}:
        row["conflicted"] = 1
    row["status"] = _status(row)


def _view(row: dict[str, Any]) -> dict[str, Any]:
    order = replace(PendingOrder.from_dict(row["request"]), filled_lots=row["filled_lots"])
    return {
        **row,
        "order": order.to_dict(),
        "quantity_lots": order.quantity_lots,
        "reservation": reservation(None if row["status"] in _TERMINAL else order),
        "requires_reconciliation": row["status"] == "UNKNOWN",
    }


class OrderJournal:
    """No credentials or authority are inferred from an authorization UUID.

    The owning Live operation must check its persisted permit, current account
    and Risk budget in ``admit``. This journal is not a public order endpoint.
    A transport callback includes the owner's final liveness check. It is never
    invoked for an already admitted identity, even after failure or restart.
    """

    def __init__(self, engine: Engine, runtime_id: UUID) -> None:
        if engine.dialect.name != "sqlite" or not isinstance(runtime_id, UUID):
            raise ValueError("order journal requires a local runtime identity")
        self._engine = engine
        self.runtime_id = runtime_id

    def get(self, order_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            return _view(_get(connection, order_id))

    def submit(
        self,
        order: PendingOrder,
        authorization_id: UUID,
        *,
        admit: Callable[[Connection], None],
        dispatch: Callable[[PendingOrder], None],
    ) -> dict[str, Any]:
        if order.filled_lots or not isinstance(authorization_id, UUID):
            raise ValueError("admission requires an unfilled order and explicit authorization")
        if str(UUID(order.order_id)) != order.order_id:
            raise ValueError("local external order identity must be a canonical UUID")
        attempt = str(uuid4())
        with write_transaction(self._engine) as connection:
            try:
                prior = _get(connection, order.order_id)
            except LookupError:
                prior = None
            if prior is not None:
                if prior["request"] != order.to_dict() or prior["authorization_id"] != str(
                    authorization_id
                ):
                    raise ValueError("order identity is already bound to different input")
                return _view(prior)
            now = datetime.now(UTC)
            if not order.submitted_at <= now < order.expires_at:
                raise ValueError("order admission is outside its fixed authorization window")
            # One net-target working order per contract, matching ExecutionEngine.
            active = connection.scalar(
                select(_orders.c.order_id)
                .where(
                    _orders.c.contract_id == str(order.contract_id),
                    _orders.c.status.not_in(_TERMINAL),
                )
                .limit(1)
            )
            if active is not None:
                raise ValueError("contract already has an unresolved execution order")
            admit(connection)
            connection.execute(
                _orders.insert().values(
                    order_id=order.order_id,
                    contract_id=str(order.contract_id),
                    authorization_id=str(authorization_id),
                    runtime_id=str(self.runtime_id),
                    attempt_id=attempt,
                    request=order.to_dict(),
                    filled_lots=0,
                    reported_lots=None,
                    broker_state=None,
                    conflicted=0,
                    status="UNKNOWN",
                )
            )
            _record(
                connection,
                attempt,
                order.order_id,
                "SEND_ATTEMPT",
                {
                    "authorization_id": str(authorization_id),
                    "runtime_id": str(self.runtime_id),
                    "request": order.to_dict(),
                },
            )
        # A crash before/inside/after this call leaves exactly the same durable
        # uncertain attempt. Neither a callback return nor an exception frees it.
        outcome = "TRANSPORT_RETURNED"
        try:
            dispatch(order)
        except Exception:
            outcome = "TRANSPORT_FAILED"
        with write_transaction(self._engine) as connection:
            _record(connection, str(uuid4()), order.order_id, outcome, {"attempt_id": attempt})
        return self.get(order.order_id)

    def cancel(
        self,
        order_id: str,
        request_id: UUID,
        *,
        admit: Callable[[Connection], None],
        dispatch: Callable[[PendingOrder], None],
    ) -> dict[str, Any]:
        """Persist a cancellation attempt without changing the order or its budget.

        A second attempt requires a separately identified broker rejection of the
        previous cancellation. Timeout, SDK failure and owner restart are not one.
        """
        if not isinstance(request_id, UUID):
            raise ValueError("cancellation requires a fixed UUID")
        with write_transaction(self._engine) as connection:
            row = _get(connection, order_id)
            prior = (
                connection.execute(select(_events).where(_events.c.event_id == str(request_id)))
                .mappings()
                .one_or_none()
            )
            if prior is not None:
                if prior["order_id"] != order_id or prior["kind"] not in {
                    "CANCEL_ATTEMPT",
                    "CANCEL_NOT_NEEDED",
                }:
                    raise ValueError("cancellation identity is bound to different input")
                return _view(row)
            if row["status"] in _TERMINAL:
                _record(connection, str(request_id), order_id, "CANCEL_NOT_NEEDED", {})
                return _view(row)
            attempted: set[str] = set()
            rejected: set[str] = set()
            for event in connection.execute(
                select(_events).where(
                    _events.c.order_id == order_id,
                    _events.c.kind.in_(("CANCEL_ATTEMPT", "CANCEL_REJECTED")),
                )
            ).mappings():
                if event["kind"] == "CANCEL_ATTEMPT":
                    attempted.add(event["event_id"])
                else:
                    rejected.add(event["document"]["attempt_id"])
            if attempted - rejected:
                raise ValueError("previous cancellation is unresolved; query before retrying")
            admit(connection)
            _record(
                connection,
                str(request_id),
                order_id,
                "CANCEL_ATTEMPT",
                {"runtime_id": str(self.runtime_id)},
            )
            order = replace(PendingOrder.from_dict(row["request"]), filled_lots=row["filled_lots"])
        outcome = "CANCEL_TRANSPORT_RETURNED"
        try:
            dispatch(order)
        except Exception:
            outcome = "CANCEL_TRANSPORT_FAILED"
        with write_transaction(self._engine) as connection:
            _record(connection, str(uuid4()), order_id, outcome, {"attempt_id": str(request_id)})
        return self.get(order_id)

    def reject_cancel(
        self,
        order_id: str,
        attempt_id: UUID,
        *,
        evidence_id: UUID,
        record_source: Callable[[Connection], None] | None = None,
    ) -> dict[str, Any]:
        """Record an adapter-verified cancellation rejection, never an order rejection."""
        if not isinstance(attempt_id, UUID) or not isinstance(evidence_id, UUID):
            raise ValueError("cancel rejection requires attempt and evidence UUIDs")
        with write_transaction(self._engine) as connection:
            row = _get(connection, order_id)
            attempt = (
                connection.execute(select(_events).where(_events.c.event_id == str(attempt_id)))
                .mappings()
                .one_or_none()
            )
            if (
                attempt is None
                or attempt["order_id"] != order_id
                or attempt["kind"] != "CANCEL_ATTEMPT"
            ):
                raise ValueError("cancel rejection has no matching local attempt")
            if record_source is not None:
                record_source(connection)
            _record(
                connection,
                str(evidence_id),
                order_id,
                "CANCEL_REJECTED",
                {"attempt_id": str(attempt_id)},
            )
            return _view(row)

    def report(
        self,
        order_id: str,
        *,
        evidence_id: UUID,
        state: str,
        cumulative_lots: int,
        record_source: Callable[[Connection], None] | None = None,
    ) -> dict[str, Any]:
        """Apply an adapter-validated report; an absent query row is never terminal.

        The adapter owns matching broker/account/order identities and retained raw
        evidence. Regressive or conflicting reports remain facts and stop release.
        """
        if (
            not isinstance(evidence_id, UUID)
            or state not in _REPORTS
            or type(cumulative_lots) is not int
        ):
            raise ValueError("order report requires identified supported broker facts")
        with write_transaction(self._engine) as connection:
            row = _get(connection, order_id)
            if record_source is not None:
                record_source(connection)
            if not _record(
                connection,
                str(evidence_id),
                order_id,
                "BROKER_REPORT",
                {
                    "state": state,
                    "cumulative_lots": cumulative_lots,
                },
            ):
                return _view(row)
            _apply_report(row, state, cumulative_lots)
            connection.execute(
                _orders.update()
                .where(_orders.c.order_id == order_id)
                .values(
                    reported_lots=cumulative_lots,
                    broker_state=state,
                    conflicted=row["conflicted"],
                    status=row["status"],
                )
            )
            return _view(row)

    def fill(
        self, fact: FillFact, *, post_account: Callable[[Connection, FillFact], None]
    ) -> dict[str, Any]:
        """Post one identified account fact and remaining budget in one transaction.

        The callback must persist the account fact using this connection, not an
        independent commit or an external side effect. Raw broker reception occurs
        before this operation and survives a projection/account posting failure.
        Authorization expiry never rejects an already confirmed fill.
        """
        with write_transaction(self._engine) as connection:
            row = _get(connection, fact.order_id)
            if not _record(
                connection, "fill:" + fact.fill_id, fact.order_id, "FILL", fact.to_dict()
            ):
                return _view(row)
            _apply_fill(row, fact)
            post_account(connection, fact)
            connection.execute(
                _orders.update()
                .where(_orders.c.order_id == fact.order_id)
                .values(
                    filled_lots=row["filled_lots"],
                    conflicted=row["conflicted"],
                    status=row["status"],
                )
            )
            return _view(row)

    def list(self, *, before: int | None = None) -> dict[str, Any]:
        if before is not None and (type(before) is not int or before <= 0):
            raise ValueError("order page requires a positive cursor")
        with self._engine.connect() as connection:
            statement = select(_orders, _events.c.sequence).join(
                _events, _events.c.event_id == _orders.c.attempt_id
            )
            if before is not None:
                statement = statement.where(_events.c.sequence < before)
            rows = list(
                connection.execute(
                    statement.order_by(_events.c.sequence.desc()).limit(101)
                ).mappings()
            )
            return {
                "orders": [
                    _view({key: value for key, value in row.items() if key != "sequence"})
                    for row in rows[:100]
                ],
                "next_before": rows[99]["sequence"] if len(rows) > 100 else None,
            }

    def detail(self, order_id: str, *, after: int = 0) -> dict[str, Any]:
        if type(after) is not int or after < 0:
            raise ValueError("order events require a nonnegative cursor")
        with self._engine.connect() as connection:
            row = _get(connection, order_id)
            events = list(
                connection.execute(
                    select(_events)
                    .where(
                        _events.c.order_id == order_id,
                        _events.c.sequence > after,
                    )
                    .order_by(_events.c.sequence)
                    .limit(101)
                ).mappings()
            )
            return {
                "record": _view(row),
                "events": [dict(event) for event in events[:100]],
                "next_after": events[99]["sequence"] if len(events) > 100 else None,
            }

    def working(self) -> tuple[PendingOrder, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(_orders)
                .where(_orders.c.status.not_in(_TERMINAL))
                .order_by(_orders.c.contract_id)
            ).mappings()
            return tuple(
                replace(PendingOrder.from_dict(row["request"]), filled_lots=row["filled_lots"])
                for row in rows
                if row["filled_lots"] < row["request"]["quantity_lots"]
            )

    def verify_all(self) -> int:
        """Rebuild every persisted projection before an owner admits new work.

        This audit asserts local order evidence, not broker reconciliation or
        account completeness. It never repairs facts or retries a transport.
        """
        count = 0
        with self._engine.connect() as connection:
            orphan = connection.scalar(
                select(_events.c.event_id)
                .where(~_events.c.order_id.in_(select(_orders.c.order_id)))
                .limit(1)
            )
            if orphan is not None:
                raise ValueError("execution event has no owned order")
            for stored in connection.execute(select(_orders)).mappings().yield_per(100):
                row = dict(stored)
                request = PendingOrder.from_dict(row["request"])
                if (
                    request.order_id != row["order_id"]
                    or str(request.contract_id) != row["contract_id"]
                    or request.filled_lots
                ):
                    raise ValueError("execution request identity is damaged")
                for name in ("runtime_id", "authorization_id", "attempt_id"):
                    UUID(row[name])
                row.update(
                    filled_lots=0,
                    reported_lots=None,
                    broker_state=None,
                    conflicted=0,
                    status="UNKNOWN",
                )
                started = returned = False
                cancellations: set[str] = set()
                cancellation_outcomes: set[str] = set()
                unresolved_cancellations: set[str] = set()
                for event in (
                    connection.execute(
                        select(_events)
                        .where(_events.c.order_id == row["order_id"])
                        .order_by(_events.c.sequence)
                    )
                    .mappings()
                    .yield_per(100)
                ):
                    kind, document = event["kind"], event["document"]
                    if kind == "SEND_ATTEMPT":
                        if (
                            started
                            or event["event_id"] != row["attempt_id"]
                            or document
                            != {
                                "authorization_id": row["authorization_id"],
                                "runtime_id": row["runtime_id"],
                                "request": row["request"],
                            }
                        ):
                            raise ValueError("execution attempt identity is damaged")
                        started = True
                    elif not started:
                        raise ValueError("execution facts precede durable admission")
                    elif kind in {"TRANSPORT_RETURNED", "TRANSPORT_FAILED"}:
                        if returned or document != {"attempt_id": row["attempt_id"]}:
                            raise ValueError("execution transport outcome is damaged")
                        returned = True
                    elif kind == "CANCEL_ATTEMPT":
                        if row["status"] in _TERMINAL or set(document) != {"runtime_id"}:
                            raise ValueError("invalid cancellation attempt")
                        UUID(document["runtime_id"])
                        if unresolved_cancellations:
                            raise ValueError("cancellation retried without a confirmed rejection")
                        cancellations.add(event["event_id"])
                        unresolved_cancellations.add(event["event_id"])
                    elif kind == "CANCEL_NOT_NEEDED":
                        if row["status"] not in _TERMINAL or document != {}:
                            raise ValueError("invalid terminal cancellation observation")
                    elif kind in {
                        "CANCEL_REJECTED",
                        "CANCEL_TRANSPORT_RETURNED",
                        "CANCEL_TRANSPORT_FAILED",
                    }:
                        attempt = document.get("attempt_id")
                        if set(document) != {"attempt_id"} or attempt not in cancellations:
                            raise ValueError("cancel observation lacks its local attempt")
                        if kind == "CANCEL_REJECTED":
                            unresolved_cancellations.discard(attempt)
                        else:
                            if attempt in cancellation_outcomes:
                                raise ValueError("duplicate cancellation transport outcome")
                            cancellation_outcomes.add(attempt)
                    elif kind == "BROKER_REPORT":
                        _apply_report(row, document["state"], document["cumulative_lots"])
                    elif kind == "FILL":
                        fact = FillFact.from_dict(document)
                        if (
                            fact.order_id != row["order_id"]
                            or event["event_id"] != "fill:" + fact.fill_id
                        ):
                            raise ValueError("execution fill identity is damaged")
                        _apply_fill(row, fact)
                    else:
                        raise ValueError("unknown execution event")
                if not started or row != dict(stored):
                    raise ValueError("execution projection differs from retained facts")
                count += 1
        return count
