"""CTP limit-order transport, with identity committed by the execution owner.

This adapter translates a fixed request; it does not size, authorize or confirm
orders. Native calls are injected by the independently supervised receiver. The
same transaction records the CTP binding and the journal's uncertain attempt.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Connection, Engine

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.broker.events import BrokerEvent
from northstar_quant.execution.journal import OrderJournal
from northstar_quant.execution.orders import Offset, PendingOrder, Side


@dataclass(frozen=True, slots=True)
class CtpSession:
    """An already verified TD login, including its counter high-water mark."""

    profile: str
    broker_id: str
    account_id: str
    trading_day: date
    front_id: int
    session_id: int
    max_order_ref: int

    def __post_init__(self) -> None:
        if self.profile not in {"simnow_trading", "simnow_dev", "ctp_production"}:
            raise ValueError("CTP session requires a fixed broker profile")
        for value, length in ((self.broker_id, 10), (self.account_id, 12)):
            if (
                not isinstance(value, str)
                or re.fullmatch(rf"[A-Za-z0-9]{{1,{length}}}", value) is None
            ):
                raise ValueError("CTP session account identity is invalid")
        if (
            type(self.trading_day) is not date
            or any(
                type(value) is not int or not 0 <= value < 2**31
                for value in (self.front_id, self.session_id)
            )
            or type(self.max_order_ref) is not int
            or not 0 <= self.max_order_ref < 10**12
        ):
            raise ValueError("CTP session identity or maximum order reference is invalid")

    @classmethod
    def from_login(
        cls, profile: str, broker_id: str, account_id: str, event: BrokerEvent
    ) -> CtpSession:
        row = event.data or {}
        reference = row.get("MaxOrderRef")
        if (
            event.channel != "TD"
            or event.callback != "OnRspUserLogin"
            or event.error_id
            or event.is_last is not True
            or row.get("BrokerID") != broker_id
            or row.get("UserID") != account_id
            or not isinstance(reference, str)
            or re.fullmatch(r"[0-9]{1,12}", reference.strip()) is None
        ):
            raise ValueError("CTP execution requires a complete matching TD login")
        return cls(
            profile,
            broker_id,
            account_id,
            date.fromisoformat(str(row.get("TradingDay"))),
            cast(int, row.get("FrontID")),
            cast(int, row.get("SessionID")),
            int(reference.strip()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "trading_day": self.trading_day.isoformat()}


def initialize(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        raise ValueError("CTP transport requires the local execution store")
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS ctp_order_bindings (
          order_id TEXT PRIMARY KEY, scope TEXT NOT NULL, order_ref INTEGER NOT NULL,
          document TEXT NOT NULL, content_hash TEXT NOT NULL,
          UNIQUE(scope, order_ref))
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS ctp_requests (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL,
          order_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('INSERT', 'CANCEL')),
          document TEXT NOT NULL, content_hash TEXT NOT NULL)
    """)
    for table in ("ctp_order_bindings", "ctp_requests"):
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                f"BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'CTP request identity is immutable'); END"
            )


def _encode(value: dict[str, Any]) -> tuple[str, str]:
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return content, hashlib.sha256(content.encode()).hexdigest()


def _decode(row: Any) -> dict[str, Any]:
    value = json.loads(row["document"])
    if not isinstance(value, dict):
        raise ValueError("CTP request must be an object")
    if _encode(value) != (row["document"], row["content_hash"]):
        raise ValueError("CTP request content differs from its fixed identity")
    return cast(dict[str, Any], value)


def _scope(session: CtpSession) -> str:
    # Never reuse an account's reference on another process or trading day.
    return _encode(
        dict(profile=session.profile, broker_id=session.broker_id, account_id=session.account_id)
    )[1]


def insert_fields(
    order: PendingOrder,
    session: CtpSession,
    instrument: dict[str, Any],
    *,
    order_ref: int,
    limit_price: Decimal,
) -> dict[str, Any]:
    """Translate only verified ordinary futures limits, with explicit close age."""
    exchange, symbol = instrument.get("ExchangeID"), instrument.get("InstrumentID")
    if (
        exchange not in {"SHFE", "INE"}
        or instrument.get("ProductClass") != "1"
        or not isinstance(symbol, str)
        or re.fullmatch(r"[A-Za-z]{1,3}[0-9]{4}", symbol) is None
    ):
        raise ValueError("CTP explicit-age execution requires a verified SHFE/INE future")
    if instrument.get("contract_id") != str(order.contract_id):
        raise ValueError("CTP instrument differs from the canonical order contract")
    minimum, maximum = instrument.get("MinLimitOrderVolume"), instrument.get("MaxLimitOrderVolume")
    if (
        type(minimum) is not int
        or type(maximum) is not int
        or not 1 <= minimum <= order.quantity_lots <= maximum <= 2**31 - 1
    ):
        raise ValueError("CTP order exceeds verified contract quantity bounds")
    raw_tick = instrument.get("PriceTick")
    if not isinstance(raw_tick, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw_tick) is None:
        raise ValueError("CTP requires verified exact tick and price")
    tick = Decimal(raw_tick)
    if not tick.is_finite() or tick <= 0 or not isinstance(limit_price, Decimal):
        raise ValueError("CTP requires verified exact tick and price")
    with localcontext() as context:
        context.prec = 192
        if (
            not limit_price.is_finite()
            or not order.minimum_fill_price <= limit_price <= order.maximum_fill_price
            or limit_price % tick
        ):
            raise ValueError("CTP limit is off tick or outside its fixed risk bounds")
    if type(order_ref) is not int or not 0 < order_ref < 10**12:
        raise ValueError("CTP order reference is outside its field capacity")
    # Native doubles are unavoidable at the SDK boundary. Refuse precision loss
    # visible on a shortest decimal round trip instead of changing the saved limit.
    if Decimal(str(float(limit_price))) != limit_price:
        raise ValueError("CTP native price cannot preserve this exact limit")
    return {
        "BrokerID": session.broker_id,
        "InvestorID": session.account_id,
        "UserID": session.account_id,
        "InstrumentID": symbol,
        "ExchangeID": exchange,
        "OrderRef": str(order_ref),
        "LimitPrice": decimal_text(limit_price),
        "VolumeTotalOriginal": order.quantity_lots,
        "Direction": "0" if order.side is Side.BUY else "1",
        "CombOffsetFlag": {Offset.OPEN: "0", Offset.CLOSE_TODAY: "3", Offset.CLOSE_YESTERDAY: "4"}[
            order.offset
        ],
        "CombHedgeFlag": "1",
        "OrderPriceType": "2",
        "TimeCondition": "3",
        "VolumeCondition": "1",
        "MinVolume": 1,
        "ContingentCondition": "1",
        "ForceCloseReason": "0",
        "IsAutoSuspend": 0,
        "UserForceClose": 0,
    }


class CtpExecution:
    """Owner-injected admission and transport; neither constructor connects a broker."""

    def __init__(self, engine: Engine, runtime_id: UUID, session: CtpSession) -> None:
        self.engine, self.journal, self.session = engine, OrderJournal(engine, runtime_id), session

    def submit(
        self,
        order: PendingOrder,
        authorization_id: UUID,
        instrument: dict[str, Any],
        limit_price: Decimal,
        *,
        admit: Callable[[Connection], None],
        send: Callable[[str, dict[str, Any], int], int],
        check_owner: Callable[[], None],
    ) -> dict[str, Any]:
        # A repeated local identity cannot silently accept a changed wire price
        # or contract, even though the generic journal request itself is unchanged.
        with self.engine.connect() as connection:
            row = (
                connection.exec_driver_sql(
                    "SELECT * FROM ctp_order_bindings WHERE order_id=?", (order.order_id,)
                )
                .mappings()
                .one_or_none()
            )
            if row is not None:
                previous = _decode(row)
                if (
                    previous["instrument"] != instrument
                    or previous["fields"]["LimitPrice"] != decimal_text(limit_price)
                    or row["scope"] != _scope(self.session)
                ):
                    raise ValueError("CTP order identity is bound to different wire input")
            elif (
                connection.exec_driver_sql(
                    "SELECT 1 FROM execution_orders WHERE order_id=?", (order.order_id,)
                ).first()
                is not None
            ):
                raise ValueError("existing execution order has no matching CTP identity")

        def prepare(connection: Connection) -> None:
            admit(connection)
            scope = _scope(self.session)
            previous = connection.exec_driver_sql(
                "SELECT max(order_ref) FROM ctp_order_bindings WHERE scope=?", (scope,)
            ).scalar_one()
            reference = max(previous or 0, self.session.max_order_ref) + 1
            fields = insert_fields(
                order, self.session, instrument, order_ref=reference, limit_price=limit_price
            )
            document = dict(
                session=self.session.to_dict(),
                instrument=instrument,
                request=order.to_dict(),
                fields=fields,
            )
            content, digest = _encode(document)
            connection.exec_driver_sql(
                "INSERT INTO ctp_order_bindings VALUES (?, ?, ?, ?, ?)",
                (order.order_id, scope, reference, content, digest),
            )
            self._request(connection, order.order_id, order.order_id, "INSERT", fields)

        return self.journal.submit(
            order,
            authorization_id,
            admit=prepare,
            dispatch=lambda _: self._send(order.order_id, "INSERT", send, check_owner),
        )

    def cancel(
        self,
        order_id: str,
        request_id: UUID,
        *,
        admit: Callable[[Connection], None],
        send: Callable[[str, dict[str, Any], int], int],
        check_owner: Callable[[], None],
    ) -> dict[str, Any]:
        def prepare(connection: Connection) -> None:
            admit(connection)
            binding = self._binding(connection, order_id)
            old = binding["session"]
            if any(
                old[key] != self.session.to_dict()[key]
                for key in ("profile", "broker_id", "account_id")
            ):
                raise ValueError("CTP cancellation belongs to another broker account")
            original = binding["fields"]
            fields = {
                key: original[key]
                for key in (
                    "BrokerID",
                    "InvestorID",
                    "UserID",
                    "InstrumentID",
                    "ExchangeID",
                    "OrderRef",
                )
            }
            # Reconnection never rewrites the original client order identity.
            fields.update(FrontID=old["front_id"], SessionID=old["session_id"], ActionFlag="0")
            self._request(connection, str(request_id), order_id, "CANCEL", fields)

        return self.journal.cancel(
            order_id,
            request_id,
            admit=prepare,
            dispatch=lambda _: self._send(str(request_id), "CANCEL", send, check_owner),
        )

    @staticmethod
    def _binding(connection: Connection, order_id: str) -> dict[str, Any]:
        row = (
            connection.exec_driver_sql(
                "SELECT * FROM ctp_order_bindings WHERE order_id=?", (order_id,)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("order has no committed CTP identity")
        return _decode(row)

    @staticmethod
    def _request(
        connection: Connection, request_id: str, order_id: str, kind: str, fields: dict[str, Any]
    ) -> None:
        content, digest = _encode(fields)
        sequence = connection.exec_driver_sql(
            "INSERT INTO ctp_requests(request_id, order_id, kind, document, content_hash) "
            "VALUES (?, ?, ?, ?, ?) RETURNING sequence",
            (request_id, order_id, kind, content, digest),
        ).scalar_one()
        # Keep request identities disjoint from the receiver's bounded login/query range.
        if sequence + 100_000 >= 2**31:
            raise ValueError("CTP request identity capacity exhausted")

    def _send(
        self,
        request_id: str,
        kind: str,
        send: Callable[[str, dict[str, Any], int], int],
        check_owner: Callable[[], None],
    ) -> None:
        with self.engine.connect() as connection:
            row = (
                connection.exec_driver_sql(
                    "SELECT * FROM ctp_requests WHERE request_id=?", (request_id,)
                )
                .mappings()
                .one()
            )
            if row["kind"] != kind:
                raise ValueError("CTP request operation changed")
            fields = _decode(row)
        check_owner()
        if kind == "INSERT":
            with self.engine.connect() as connection:
                order = PendingOrder.from_dict(
                    self._binding(connection, row["order_id"])["request"]
                )
            if not order.submitted_at <= datetime.now(UTC) < order.expires_at:
                raise ValueError("CTP send is outside its fixed order lifetime")
        code = send(
            "ReqOrderInsert" if kind == "INSERT" else "ReqOrderAction",
            fields,
            row["sequence"] + 100_000,
        )
        if type(code) is not int or code != 0:
            # Do not release an order from an immediate native return; the full
            # callback and external inquiry are the authoritative resolution.
            raise ValueError("CTP transport did not establish an accepted request")


def native_request(structures: Any, method: str, fields: dict[str, Any]) -> Any:
    """Build native structures only inside the SDK child; never starts or sends."""
    if method == "ReqOrderInsert":
        values = {**fields, "LimitPrice": float(Decimal(fields["LimitPrice"]))}
        return structures.InputOrderField(**values)
    if method == "ReqOrderAction":
        return structures.InputOrderActionField(**fields)
    raise ValueError("unsupported CTP order operation")


def verify_all(engine: Engine) -> int:
    """Rebuild wire identities from fixed domain requests, without any native call."""
    count = 0
    with engine.connect() as connection:
        for row in connection.exec_driver_sql("SELECT * FROM ctp_order_bindings").mappings():
            value = _decode(row)
            session_value = dict(value["session"])
            session_value["trading_day"] = date.fromisoformat(session_value["trading_day"])
            session = CtpSession(**session_value)
            order = PendingOrder.from_dict(value["request"])
            parent = connection.exec_driver_sql(
                "SELECT request FROM execution_orders WHERE order_id=?", (row["order_id"],)
            ).scalar_one_or_none()
            if (
                parent is None
                or json.loads(parent) != value["request"]
                or row["order_id"] != order.order_id
                or row["scope"] != _scope(session)
                or row["order_ref"] <= session.max_order_ref
                or value["fields"]
                != insert_fields(
                    order,
                    session,
                    value["instrument"],
                    order_ref=row["order_ref"],
                    limit_price=Decimal(value["fields"]["LimitPrice"]),
                )
            ):
                raise ValueError("CTP identity differs from its execution parent")
            requests = (
                connection.exec_driver_sql(
                    "SELECT * FROM ctp_requests WHERE order_id=? ORDER BY sequence",
                    (row["order_id"],),
                )
                .mappings()
                .all()
            )
            inserts = 0
            for request in requests:
                fields = _decode(request)
                if request["sequence"] + 100_000 >= 2**31:
                    raise ValueError("CTP native request identity is outside capacity")
                if request["kind"] == "INSERT":
                    inserts += 1
                    if request["request_id"] != row["order_id"] or fields != value["fields"]:
                        raise ValueError("CTP insert differs from its fixed request")
                else:
                    expected = {
                        key: value["fields"][key]
                        for key in (
                            "BrokerID",
                            "InvestorID",
                            "UserID",
                            "InstrumentID",
                            "ExchangeID",
                            "OrderRef",
                        )
                    }
                    expected.update(
                        FrontID=session.front_id, SessionID=session.session_id, ActionFlag="0"
                    )
                    event = connection.exec_driver_sql(
                        "SELECT kind FROM execution_order_events WHERE event_id=? AND order_id=?",
                        (request["request_id"], row["order_id"]),
                    ).scalar_one_or_none()
                    if event != "CANCEL_ATTEMPT" or fields != expected:
                        raise ValueError("CTP cancellation differs from its execution parent")
            if inserts != 1:
                raise ValueError("CTP binding requires exactly one insert attempt")
            count += 1
        if (
            connection.exec_driver_sql(
                "SELECT 1 FROM ctp_requests r LEFT JOIN ctp_order_bindings b "
                "ON r.order_id=b.order_id WHERE b.order_id IS NULL LIMIT 1"
            ).first()
            is not None
        ):
            raise ValueError("CTP request has no fixed order binding")
    return count
