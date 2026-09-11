"""Associate retained CTP order reports with their committed local wire identity."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import Connection, Engine

from northstar_quant.broker.order_transport import _decode, _encode
from northstar_quant.broker.stream_records import read_stream_event, read_stream_source
from northstar_quant.execution.journal import OrderJournal


def initialize(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS ctp_order_receipts (
            event_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
            stream_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            event_hash TEXT NOT NULL, system_id TEXT,
            UNIQUE(stream_id, sequence),
            FOREIGN KEY(order_id) REFERENCES ctp_order_bindings(order_id))
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS ctp_exchange_orders (
            order_id TEXT PRIMARY KEY, exchange_key TEXT UNIQUE NOT NULL,
            system_id TEXT NOT NULL, event_id TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES ctp_order_bindings(order_id),
            FOREIGN KEY(event_id) REFERENCES ctp_order_receipts(event_id))
    """)
    for table in ("ctp_order_receipts", "ctp_exchange_orders"):
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                f"BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'CTP receipt identity is immutable'); END"
            )


def _report(connection: Connection, stream_id: UUID, sequence: int) -> dict[str, Any] | None:
    event = read_stream_event(connection, stream_id, sequence)
    if event.channel != "TD" or event.callback != "OnRtnOrder":
        return None
    if connection.exec_driver_sql("SELECT 1 FROM ctp_order_bindings LIMIT 1").first() is None:
        return None
    source = read_stream_source(connection, stream_id)["binding"]
    assert isinstance(source, dict)
    row = dict(event.data or {})
    if (
        event.error_id
        or row.get("BrokerID") != source["profile"]["broker_id"]
        or row.get("InvestorID") != source["account_id"]
    ):
        raise ValueError("CTP order report account differs from its received source")
    ref = row.get("OrderRef")
    if (
        not isinstance(ref, str)
        or not 1 <= len(ref.strip()) <= 12
        or not ref.strip().isascii()
        or not ref.strip().isdigit()
    ):
        return None  # External/unknown identities remain source evidence for reconciliation.
    scope = _encode(
        dict(
            profile=source["profile"]["name"],
            broker_id=row["BrokerID"],
            account_id=row["InvestorID"],
        )
    )[1]
    binding = (
        connection.exec_driver_sql(
            "SELECT * FROM ctp_order_bindings WHERE scope=? AND order_ref=?",
            (scope, int(ref.strip())),
        )
        .mappings()
        .one_or_none()
    )
    if binding is None:
        return None
    value = _decode(binding)
    session, fields = value["session"], value["fields"]
    if (row.get("FrontID"), row.get("SessionID")) != (session["front_id"], session["session_id"]):
        return None  # OrderRef alone cannot identify an order across logins.
    if row.get("TradingDay") != session["trading_day"].replace("-", "") or any(
        row.get(key) != fields[key]
        for key in (
            "InstrumentID",
            "ExchangeID",
            "Direction",
            "CombOffsetFlag",
            "CombHedgeFlag",
            "OrderPriceType",
            "TimeCondition",
            "VolumeCondition",
            "MinVolume",
            "VolumeTotalOriginal",
        )
    ):
        raise ValueError("CTP order report differs from its fixed request")
    price = row.get("LimitPrice")
    if not isinstance(price, str) or Decimal(price) != Decimal(fields["LimitPrice"]):
        raise ValueError("CTP order report changed the fixed limit")
    traded, remaining, original = (
        row.get("VolumeTraded"),
        row.get("VolumeTotal"),
        fields["VolumeTotalOriginal"],
    )
    if (
        type(traded) is not int
        or type(remaining) is not int
        or traded < 0
        or remaining < 0
        or traded + remaining != original
    ):
        raise ValueError("CTP cumulative order quantities do not conserve the request")
    status, submit = row.get("OrderStatus"), row.get("OrderSubmitStatus")
    if not isinstance(status, str) or not isinstance(submit, str):
        raise ValueError("CTP order status is malformed")
    state = {"0": "FILLED", "1": "PARTIALLY_FILLED", "3": "ACCEPTED", "5": "CANCELED"}.get(
        status, "UNKNOWN"
    )
    if (
        status == "0"
        and traded != original
        or status == "1"
        and not 0 < traded < original
        or status == "3"
        and traded != 0
        or status == "5"
        and traded >= original
    ):
        raise ValueError("CTP order state contradicts its quantities")
    if submit == "4":
        if status != "5" or traded:
            raise ValueError("CTP insert rejection contradicts its reported fills")
        state = "REJECTED"
    elif submit not in {"1", "3", "5"}:
        state = "UNKNOWN"
    raw_system = row.get("OrderSysID")
    if not isinstance(raw_system, str) or len(raw_system) > 21 or not raw_system.isascii():
        raise ValueError("CTP exchange order identity is malformed")
    system = raw_system.strip() or None
    event_id = str(uuid5(stream_id, f"ctp-order:{sequence}"))
    return dict(
        event_id=event_id,
        order_id=binding["order_id"],
        stream_id=str(stream_id),
        sequence=sequence,
        event_hash=_encode(event.to_dict())[1],
        system_id=system,
        state=state,
        cumulative_lots=traded,
        exchange_key=None
        if system is None
        else _encode(
            dict(
                scope=scope,
                trading_day=row["TradingDay"],
                exchange=row["ExchangeID"],
                system_id=system,
            )
        )[1],
    )


def _save(connection: Connection, report: dict[str, Any]) -> None:
    keys = ("event_id", "order_id", "stream_id", "sequence", "event_hash", "system_id")
    prior = (
        connection.exec_driver_sql(
            "SELECT * FROM ctp_order_receipts WHERE event_id=?", (report["event_id"],)
        )
        .mappings()
        .one_or_none()
    )
    if prior is not None:
        if dict(prior) != {key: report[key] for key in keys}:
            raise ValueError("CTP receipt is bound to different source evidence")
    else:
        connection.exec_driver_sql(
            "INSERT INTO ctp_order_receipts VALUES (?, ?, ?, ?, ?, ?)",
            tuple(report[key] for key in keys),
        )
    if report["system_id"] is not None:
        prior = (
            connection.exec_driver_sql(
                "SELECT * FROM ctp_exchange_orders WHERE order_id=?", (report["order_id"],)
            )
            .mappings()
            .one_or_none()
        )
        if prior is None:
            connection.exec_driver_sql(
                "INSERT INTO ctp_exchange_orders VALUES (?, ?, ?, ?)",
                tuple(report[key] for key in ("order_id", "exchange_key", "system_id", "event_id")),
            )
        elif (prior["exchange_key"], prior["system_id"]) != (
            report["exchange_key"],
            report["system_id"],
        ):
            raise ValueError("CTP exchange identity changed for the local order")


def apply_stream(engine: Engine, stream_id: UUID, sequence: int) -> str | None:
    """Project a retained receipt; duplicate catch-up cannot release or resend twice."""
    with engine.connect() as connection:
        report = _report(connection, stream_id, sequence)
    if report is None:
        return None

    def save(connection: Connection) -> None:
        checked = _report(connection, stream_id, sequence)
        if checked != report:
            raise ValueError("CTP receipt changed during projection")
        _save(connection, report)

    OrderJournal(engine, UUID(int=0)).report(
        report["order_id"],
        evidence_id=UUID(report["event_id"]),
        state=report["state"],
        cumulative_lots=report["cumulative_lots"],
        record_source=save,
    )
    return str(report["order_id"])


def verify_all(engine: Engine) -> int:
    count = 0
    with engine.connect() as connection:
        for receipt in connection.exec_driver_sql("SELECT * FROM ctp_order_receipts").mappings():
            report = _report(connection, UUID(receipt["stream_id"]), receipt["sequence"])
            if report is None or any(report[key] != receipt[key] for key in receipt):
                raise ValueError("CTP receipt no longer matches its source")
            parent = (
                connection.exec_driver_sql(
                    "SELECT kind, document FROM execution_order_events "
                    "WHERE event_id=? AND order_id=?",
                    (receipt["event_id"], receipt["order_id"]),
                )
                .mappings()
                .one_or_none()
            )
            if (
                parent is None
                or parent["kind"] != "BROKER_REPORT"
                or json.loads(parent["document"])
                != {"state": report["state"], "cumulative_lots": report["cumulative_lots"]}
            ):
                raise ValueError("CTP receipt has no matching execution report")
            if report["system_id"] is not None:
                exchange = connection.exec_driver_sql(
                    "SELECT exchange_key, system_id FROM ctp_exchange_orders WHERE order_id=?",
                    (report["order_id"],),
                ).one_or_none()
                if exchange is None or tuple(exchange) != (
                    report["exchange_key"],
                    report["system_id"],
                ):
                    raise ValueError("CTP receipt has no matching exchange identity")
            count += 1
        for row in connection.exec_driver_sql("SELECT * FROM ctp_exchange_orders").mappings():
            original_receipt = (
                connection.exec_driver_sql(
                    "SELECT * FROM ctp_order_receipts WHERE event_id=?", (row["event_id"],)
                )
                .mappings()
                .one_or_none()
            )
            if original_receipt is None:
                raise ValueError("CTP exchange order has no original receipt")
            report = _report(
                connection, UUID(original_receipt["stream_id"]), original_receipt["sequence"]
            )
            if report is None or any(row[key] != report[key] for key in row):
                raise ValueError("CTP exchange order differs from its original receipt")
    return count
