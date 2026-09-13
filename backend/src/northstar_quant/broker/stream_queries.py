"""Reconstruct the receiver's initial query from its own retained callbacks."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import Engine

from .events import MAX_CAPTURE_BYTES, MAX_EVENTS, BrokerEvent, QueryCapture, canonical_bytes
from .query_projection import project_query
from .stream_records import read_stream_source, text


def startup_query(engine: Engine, identifier: UUID) -> dict[str, Any]:
    """Return historical startup evidence, never a current account certificate.

    The native receiver performs its standard queries before subscribing to MD.
    Stop at that first subscription response; later fills, disconnects or logins
    cannot silently rewrite the original observation. No SDK or catalog writes.
    """
    with engine.connect() as connection:
        source = read_stream_source(connection, identifier)
        binding = source["binding"]
        assert isinstance(binding, dict)
        digest = hashlib.sha256(
            canonical_bytes({"stream_id": str(identifier), "binding_hash": source["binding_hash"]})
        )
        events: list[BrokerEvent] = []
        size = 0
        complete = False
        records = connection.execute(
            text(
                "SELECT sequence,event,event_hash,committed_at FROM broker_stream_events "
                "WHERE stream_id=:id AND sequence<=:through ORDER BY sequence LIMIT :limit"
            ),
            {"id": identifier, "through": source["received"], "limit": MAX_EVENTS + 1},
        ).mappings()
        for row in records:
            event = BrokerEvent.from_dict(row["event"])
            encoded = json.dumps(
                event.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
            if (
                event.sequence != len(events) + 1
                or row["sequence"] != event.sequence
                or hashlib.sha256(encoded).hexdigest() != row["event_hash"]
            ):
                raise ValueError("receiver startup query source is missing or damaged")
            size += len(encoded)
            if len(events) == MAX_EVENTS or size > MAX_CAPTURE_BYTES - 4096:
                return {
                    "status": "UNAVAILABLE",
                    "reason": "STARTUP_QUERY_LIMIT",
                    "through_sequence": len(events),
                    "source_hash": digest.hexdigest(),
                    "completeness": {},
                    "execution": {"order_sending": False},
                }
            events.append(event)
            digest.update(
                canonical_bytes(
                    [event.sequence, row["event_hash"], row["committed_at"].isoformat()]
                )
            )
            if (
                event.channel == "MD"
                and event.callback == "OnRspSubMarketData"
                and event.is_last is True
            ):
                complete = True
                break
        if not complete and len(events) != source["received"]:
            raise ValueError("receiver startup query has missing retained callbacks")
    if not events:
        return {
            "status": "PENDING",
            "reason": "STARTUP_QUERY_NOT_OBSERVED",
            "through_sequence": 0,
            "source_hash": digest.hexdigest(),
            "completeness": {},
            "execution": {"order_sending": False},
        }
    # Versions were not individual stream callbacks; leave them explicitly unknown.
    # A finite view of the original prefix does not turn it into a separate query.
    try:
        capture = QueryCapture(
            events[0].received_at, events[-1].received_at, None, None, None, None, tuple(events)
        )
    except ValueError:
        return {
            "status": "UNAVAILABLE",
            "reason": "STARTUP_QUERY_INTERVAL_INVALID",
            "through_sequence": len(events),
            "source_hash": digest.hexdigest(),
            "completeness": {},
            "execution": {"order_sending": False},
        }
    projected = project_query({**binding, "query_scope": {}}, capture)
    return {
        "status": projected["status"]
        if complete or projected["status"] == "FAILED"
        else "INCOMPLETE",
        "reason": "FIXED_STARTUP_OBSERVATION" if complete else "STARTUP_QUERY_NOT_FINISHED",
        "through_sequence": len(events),
        "source_hash": digest.hexdigest(),
        "started_at": capture.started_at,
        "finished_at": capture.finished_at,
        "completeness": projected["completeness"],
        "reconciliation": projected["reconciliation"],
        "execution": {"order_sending": False},
    }
