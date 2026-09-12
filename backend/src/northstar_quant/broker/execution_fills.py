"""Verified exchange-order associations turn retained CTP trades into local fills.

Unmatched trades remain original account evidence. They never get an invented
local order, fee or cash balance. Position progress and the local OMS fill share
one writer transaction; repeated reception adds evidence, not another fill.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import Connection, Engine

from northstar_quant.accounting.fills import FillFact
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.account_reports import decode_trade
from northstar_quant.broker.order_transport import _decode, _encode
from northstar_quant.broker.stream_records import read_stream_event, read_stream_source
from northstar_quant.execution.journal import OrderJournal
from northstar_quant.execution.orders import Offset, Side


def initialize(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS ctp_fill_facts (
            fill_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
            document TEXT NOT NULL, content_hash TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES ctp_order_bindings(order_id))
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS ctp_fill_receipts (
            event_id TEXT PRIMARY KEY, fill_id TEXT NOT NULL,
            stream_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_hash TEXT NOT NULL,
            UNIQUE(stream_id, sequence),
            FOREIGN KEY(fill_id) REFERENCES ctp_fill_facts(fill_id))
    """)
    for table in ("ctp_fill_facts", "ctp_fill_receipts"):
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                f"BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'CTP fill evidence is immutable'); END"
            )


def _match(connection: Connection, stream_id: UUID, sequence: int) -> tuple[FillFact, str] | None:
    event = read_stream_event(connection, stream_id, sequence)
    if event.channel != "TD" or event.callback != "OnRtnTrade":
        return None
    if connection.exec_driver_sql("SELECT 1 FROM ctp_exchange_orders LIMIT 1").first() is None:
        return None
    source = read_stream_source(connection, stream_id)["binding"]
    assert isinstance(source, dict)
    if event.error_id:
        raise ValueError("CTP execution callback contains an error")
    trade = decode_trade(dict(event.data or {}), source)
    scope = _encode(
        dict(
            profile=source["profile"]["name"],
            broker_id=source["profile"]["broker_id"],
            account_id=source["account_id"],
        )
    )[1]
    exchange_key = _encode(
        dict(
            scope=scope,
            trading_day=trade["trading_day"],
            exchange=trade["exchange"],
            system_id=trade["order_sys_id"].strip(),
        )
    )[1]
    row = (
        connection.exec_driver_sql(
            """
        SELECT b.* FROM ctp_exchange_orders e JOIN ctp_order_bindings b USING(order_id)
        WHERE e.exchange_key=?
    """,
            (exchange_key,),
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    binding = _decode(row)
    session, fields = binding["session"], binding["fields"]
    if (
        row["scope"] != scope
        or source["contract_id"] != binding["request"]["contract_id"]
        or trade["trading_day"] != session["trading_day"].replace("-", "")
        or trade["symbol"] != fields["InstrumentID"].upper()
        or trade["exchange"] != fields["ExchangeID"]
        or trade["hedge_flag"] != "1"
        or trade["offset_flag"] != fields["CombOffsetFlag"]
        or (event.data or {}).get("Direction") != fields["Direction"]
    ):
        raise ValueError("CTP execution differs from its fixed exchange order")
    fact = FillFact(
        trade["fill_id"],
        row["order_id"],
        UUID(source["contract_id"]),
        None,
        datetime.fromisoformat(trade["filled_at"]),
        date.fromisoformat(trade["trading_day"]),
        Side(trade["direction"]),
        Offset(trade["offset"]),
        trade["quantity_lots"],
        Decimal(trade["price"]),
        None,
        datetime.fromisoformat(event.received_at),
    )
    return fact, _encode(event.to_dict())[1]


def _saved(connection: Connection, fill_id: str) -> dict[str, Any] | None:
    row = (
        connection.exec_driver_sql("SELECT * FROM ctp_fill_facts WHERE fill_id=?", (fill_id,))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    value = _decode(row)
    fact = FillFact.from_dict(value["fact"])
    if fact.fill_id != row["fill_id"] or fact.order_id != row["order_id"]:
        raise ValueError("CTP fill identity is damaged")
    return value


def apply_stream(engine: Engine, stream_id: UUID, sequence: int) -> str | None:
    with engine.connect() as connection:
        matched = _match(connection, stream_id, sequence)
        if matched is None:
            return None
        received, event_hash = matched
        previous = _saved(connection, received.fill_id)
    fact = received if previous is None else FillFact.from_dict(previous["fact"])
    if replace(received, available_at=fact.available_at) != fact:
        raise ValueError("CTP execution identity was repeated with conflicting facts")
    event_id = str(uuid5(stream_id, f"ctp-fill:{sequence}"))

    def post_account(connection: Connection, accepted: FillFact) -> None:
        progress = BrokerLedger(engine).advance_stream(stream_id, sequence, transaction=connection)
        if progress["entry_id"] is None:
            raise ValueError("local CTP fill requires a bound retained account prefix")
        document = dict(
            fact=accepted.to_dict(),
            source_stream_id=str(stream_id),
            source_sequence=sequence,
            account_entry_id=progress["entry_id"],
        )
        content, digest = _encode(document)
        connection.exec_driver_sql(
            "INSERT INTO ctp_fill_facts VALUES (?, ?, ?, ?)",
            (accepted.fill_id, accepted.order_id, content, digest),
        )

    def save_receipt(connection: Connection) -> None:
        checked = _match(connection, stream_id, sequence)
        if checked != matched:
            raise ValueError("CTP execution source changed while posting")
        canonical = _saved(connection, fact.fill_id)
        if canonical is None or FillFact.from_dict(canonical["fact"]) != fact:
            raise ValueError("CTP execution lacks its canonical account fact")
        fields = (event_id, fact.fill_id, str(stream_id), sequence, event_hash)
        old = connection.exec_driver_sql(
            "SELECT * FROM ctp_fill_receipts WHERE event_id=?", (event_id,)
        ).first()
        if old is None:
            connection.exec_driver_sql(
                "INSERT INTO ctp_fill_receipts VALUES (?, ?, ?, ?, ?)", fields
            )
        elif tuple(old) != fields:
            raise ValueError("CTP execution receipt conflicts with retained evidence")

    OrderJournal(engine, UUID(int=0)).fill(
        fact, post_account=post_account, record_source=save_receipt
    )
    return fact.fill_id


def apply_pending(
    engine: Engine, stream_id: UUID, through_sequence: int, *, order_id: str | None = None
) -> int:
    """Catch up saved trades after their order identity becomes known, without network I/O."""
    if (
        not isinstance(stream_id, UUID)
        or type(through_sequence) is not int
        or not 1 <= through_sequence <= 100000
    ):
        raise ValueError("CTP fill catchup requires a bounded saved prefix")
    with engine.connect() as connection:
        rows = (
            connection.exec_driver_sql(
                """
            SELECT e.sequence FROM broker_stream_events e
            WHERE e.stream_id=? AND e.sequence<=?
              AND json_extract(e.event, '$.callback')='OnRtnTrade'
              AND NOT EXISTS (SELECT 1 FROM ctp_fill_receipts r
                              WHERE r.stream_id=? AND r.sequence=e.sequence)
              AND (? IS NULL OR trim(json_extract(e.event, '$.data.OrderSysID'))=
                    (SELECT system_id FROM ctp_exchange_orders WHERE order_id=?))
            ORDER BY e.sequence
        """,
                (stream_id.hex, through_sequence, str(stream_id), order_id, order_id),
            )
            .scalars()
            .all()
        )
    return sum(apply_stream(engine, stream_id, sequence) is not None for sequence in rows)


def verify_all(engine: Engine) -> int:
    ledger = BrokerLedger(engine)
    count = 0
    with engine.connect() as connection:
        for row in connection.exec_driver_sql("SELECT * FROM ctp_fill_facts").mappings():
            saved = _saved(connection, row["fill_id"])
            assert saved is not None
            fact = FillFact.from_dict(saved["fact"])
            first = _match(connection, UUID(saved["source_stream_id"]), saved["source_sequence"])
            if first is None or first[0] != fact:
                raise ValueError("CTP fill differs from its original receipt")
            parent = connection.exec_driver_sql(
                "SELECT kind, order_id, document FROM execution_order_events WHERE event_id=?",
                ("fill:" + fact.fill_id,),
            ).first()
            if (
                parent is None
                or parent[0] != "FILL"
                or parent[1] != fact.order_id
                or json.loads(parent[2]) != fact.to_dict()
            ):
                raise ValueError("CTP fill lacks its matching OMS fact")
            accounted = ledger.get_fill(UUID(saved["account_entry_id"]), fact.fill_id)
            source_id = UUID(saved["source_stream_id"])
            source = read_stream_source(connection, source_id)["binding"]
            assert isinstance(source, dict)
            raw = read_stream_event(connection, source_id, saved["source_sequence"])
            expected = {
                **decode_trade(dict(raw.data or {}), source),
                "contract_id": str(fact.contract_id),
            }
            if any(accounted[key] != value for key, value in expected.items()):
                raise ValueError("CTP fill differs from its retained account fact")
            original = connection.exec_driver_sql(
                "SELECT 1 FROM ctp_fill_receipts WHERE stream_id=? AND sequence=? AND fill_id=?",
                (saved["source_stream_id"], saved["source_sequence"], fact.fill_id),
            ).first()
            if original is None:
                raise ValueError("CTP fill lacks its original account receipt")
            count += 1
        for row in connection.exec_driver_sql("SELECT * FROM ctp_fill_receipts").mappings():
            matched = _match(connection, UUID(row["stream_id"]), row["sequence"])
            saved = _saved(connection, row["fill_id"])
            if matched is None or saved is None:
                raise ValueError("CTP fill receipt lacks its fixed association")
            fact = FillFact.from_dict(saved["fact"])
            if (
                replace(matched[0], available_at=fact.available_at) != fact
                or matched[1] != row["event_hash"]
                or row["event_id"]
                != str(uuid5(UUID(row["stream_id"]), f"ctp-fill:{row['sequence']}"))
            ):
                raise ValueError("CTP fill receipt identity or content differs")
    return count
