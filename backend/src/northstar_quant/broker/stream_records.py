"""Retained CTP stream receipts and bounded verified exports for account and Data owners."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import cast
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from northstar_quant.broker.records import BrokerEvent, BrokerRecords
from northstar_quant.data_management.broker import verify_broker_contract

_ARCHIVE_BYTES = 5 * 1024 * 1024


def read_stream_source(connection: Connection, identifier: UUID) -> dict[str, object]:
    """Read immutable source binding and receipt boundary, without a Live checkpoint."""
    row = (
        connection.execute(
            text("""
        SELECT binding, binding_hash, received, byte_count FROM broker_streams WHERE stream_id=:id
    """),
            {"id": identifier},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("stream not found")
    result = dict(row)
    if _hash(result["binding"]) != result["binding_hash"]:
        raise ValueError("stream binding integrity failed")
    return result


def append_stream_event(
    connection: Connection, identifier: UUID, event: BrokerEvent, *, receiving: bool
) -> None:
    """Append within the caller's locked session transaction; repeated receipts are immutable."""
    row = read_stream_source(connection, identifier)
    encoded = event.to_dict()
    previous = connection.execute(
        text("""
        SELECT event FROM broker_stream_events WHERE stream_id=:id AND sequence=:seq
    """),
        {"id": identifier, "seq": event.sequence},
    ).scalar_one_or_none()
    if previous is not None:
        if previous != encoded:
            raise ValueError("stream event identity conflicts with retained content")
        return
    if not receiving or event.sequence != cast(int, row["received"]) + 1:
        raise ValueError("stream must receive contiguous events while connected")
    size = len(_json(encoded).encode())
    if cast(int, row["byte_count"]) + size > 128 * 1024 * 1024:
        raise ValueError("stream retained callback limit exceeded")
    connection.execute(
        text("""
        INSERT INTO broker_stream_events(stream_id, sequence, event, event_hash)
        VALUES (:id, :seq, CAST(:event AS jsonb), :hash)
    """),
        {"id": identifier, "seq": event.sequence, "event": _json(encoded), "hash": _hash(encoded)},
    )
    connection.execute(
        text("""
        UPDATE broker_streams SET received=:seq, byte_count=byte_count+:size,
            updated_at=clock_timestamp() WHERE stream_id=:id
    """),
        {"id": identifier, "seq": event.sequence, "size": size},
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("stream content must be an object")
    return cast(dict[str, object], value)


def read_stream_archive(engine: Engine, identifier: UUID, through_sequence: int) -> bytes:
    """Copy an exact committed prefix for Data, without connecting or projecting.

    The immutable binding and every original callback/receipt are retained,
    including non-market callbacks needed to explain identity and interruption.
    Mutable shadow checkpoints and control state are deliberately excluded: a
    later callback, pause or restart cannot change a previously selected prefix.
    The 5 MiB bound applies to the full JSON document; never silently truncate.
    """
    if type(through_sequence) is not int or not 1 <= through_sequence <= 100000:
        raise ValueError("archive prefix must end at a positive committed sequence")
    with engine.connect() as connection:
        row = read_stream_source(connection, identifier)
        if through_sequence > cast(int, row["received"]):
            raise ValueError("archive prefix cannot include unreceived callbacks")
        archive: dict[str, object] = {
            "kind": "COPIED_CTP_CALLBACK_PREFIX",
            "stream_id": str(identifier),
            "through_sequence": through_sequence,
            "binding": row["binding"],
            "binding_hash": row["binding_hash"],
            "events": [],
        }
        events: list[dict[str, object]] = []
        size = len(_json(archive).encode())
        records = (
            connection.execution_options(yield_per=100)
            .execute(
                text("""
                SELECT sequence, event, event_hash, committed_at FROM broker_stream_events
                WHERE stream_id=:id AND sequence<=:through ORDER BY sequence
            """),
                {"id": identifier, "through": through_sequence},
            )
            .mappings()
        )
        for item in records:
            event = _object(item["event"])
            if (
                item["sequence"] != len(events) + 1
                or event.get("sequence") != item["sequence"]
                or _hash(event) != item["event_hash"]
            ):
                raise ValueError("stream archive source sequence or content integrity failed")
            entry = {
                "event": event,
                "event_hash": item["event_hash"],
                "committed_at": item["committed_at"].astimezone(UTC).isoformat(),
            }
            size += len(_json(entry).encode()) + (1 if events else 0)
            if size > _ARCHIVE_BYTES:
                raise ValueError(
                    "stream archive prefix exceeds 5 MiB; select an earlier committed "
                    "sequence and a smaller explicit time range"
                )
            events.append(entry)
        if len(events) != through_sequence:
            raise ValueError("stream archive prefix is missing committed callbacks")
    archive["events"] = events
    content = _json(archive).encode()
    if len(content) > _ARCHIVE_BYTES:
        raise ValueError("stream archive prefix exceeds 5 MiB")
    return content


def read_stream_account_prefix(
    engine: Engine, identifier: UUID, through_sequence: int, *, after_sequence: int = 0
) -> dict[str, object]:
    """Read bounded TD evidence while hashing every callback in a fixed prefix.

    This is not a query result. Its hash covers original content and commit times,
    including MD callbacks, without accumulating their payloads in memory. The
    caller may identify the start of a later contiguous segment, but receives the
    prefix's earlier TD identity evidence too. Mutable run/control state is not an
    input; paused, stopped and unprocessed durable callbacks remain readable.
    """
    if (
        not isinstance(identifier, UUID)
        or type(through_sequence) is not int
        or not 1 <= through_sequence <= 100000
        or type(after_sequence) is not int
        or not 0 <= after_sequence < through_sequence
    ):
        raise ValueError("account prefix requires a bounded contiguous retained sequence")
    with engine.connect() as connection:
        row = read_stream_source(connection, identifier)
        if through_sequence > cast(int, row["received"]):
            raise ValueError("account prefix cannot include unreceived callbacks")
        binding = _object(row["binding"])
        prefix: dict[str, object] = {
            "stream_id": str(identifier),
            "through_sequence": through_sequence,
            "binding": binding,
            "binding_hash": row["binding_hash"],
        }
        digest = hashlib.sha256(_json(prefix).encode())
        retained: list[dict[str, object]] = []
        count = size = 0
        times: dict[str, str] = {}
        records = (
            connection.execution_options(yield_per=100)
            .execute(
                text("""
                SELECT sequence, event, event_hash, committed_at FROM broker_stream_events
                WHERE stream_id=:id AND sequence<=:through ORDER BY sequence
            """),
                {"id": identifier, "through": through_sequence},
            )
            .mappings()
        )
        for item in records:
            count += 1
            event = BrokerEvent.from_dict(_object(item["event"]))
            encoded = event.to_dict()
            if (
                item["sequence"] != count
                or event.sequence != count
                or _hash(encoded) != item["event_hash"]
            ):
                raise ValueError("account prefix sequence or source integrity failed")
            committed = item["committed_at"].astimezone(UTC).isoformat()
            digest.update(_json([count, item["event_hash"], committed]).encode())
            size += len(_json(encoded).encode())
            if size > 128 * 1024 * 1024:
                raise ValueError("account prefix exceeds the retained stream size limit")
            if count == 1:
                times.update(first_received_at=event.received_at, first_committed_at=committed)
            if count == after_sequence + 1:
                times.update(segment_received_at=event.received_at, segment_committed_at=committed)
            times.update(last_received_at=event.received_at, last_committed_at=committed)
            if event.channel == "TD" or event.callback in {"OnRtnTrade", "OnRtnOrder"}:
                if len(retained) >= 10000:
                    raise ValueError("account prefix exceeds 10000 retained account callbacks")
                retained.append({"event": encoded, "committed_at": committed})
        if count != through_sequence:
            raise ValueError("account prefix is missing retained callbacks")
    query = BrokerRecords(engine).get(UUID(str(_object(binding["request"])["query_batch_id"])))
    if any(query[key] != binding[key] for key in ("profile", "account_id", "instrument")):
        raise ValueError("account prefix differs from its fixed query identity")
    verify_broker_contract(engine, UUID(str(binding["contract_id"])), _object(binding["terms"]))
    return {**prefix, **times, "prefix_hash": digest.hexdigest(), "events": retained}
