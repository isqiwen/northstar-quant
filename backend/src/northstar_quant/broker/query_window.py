"""A retained receiver query window with explicit original session references."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine

from .account_reports import stream_account_observation
from .events import MAX_CAPTURE_BYTES, MAX_EVENTS, BrokerEvent, canonical_bytes
from .query_projection import project_query
from .stream_queries import startup_query
from .stream_records import read_stream_source, text


@dataclass(frozen=True)
class _Window:
    events: tuple[BrokerEvent, ...]
    source: dict[str, object]
    failure_code: str | None
    trader_api_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        # A selection of original receipts is not an independent QueryCapture.
        return self.source


def latest_query(engine: Engine | Connection, identifier: UUID) -> dict[str, Any] | None:
    with engine.connect() if isinstance(engine, Engine) else nullcontext(engine) as connection:
        source = read_stream_source(connection, identifier)
        marker = connection.execute(
            text(
                "SELECT sequence FROM broker_stream_events WHERE stream_id=:id "
                "AND sequence<=:through AND event->>'callback'='AccountQueryStarted' "
                "ORDER BY sequence DESC LIMIT 1"
            ),
            {"id": identifier, "through": source["received"]},
        ).scalar_one_or_none()
        if marker is None:
            return None
        startup = startup_query(connection, identifier)
        context_end = startup["through_sequence"]
        interrupted = connection.execute(
            text(
                "SELECT sequence FROM broker_stream_events WHERE stream_id=:id "
                "AND sequence>:start AND sequence<:end AND event->>'callback' IN "
                "('OnRspUserLogin','OnFrontDisconnected','OnHeartBeatWarning') LIMIT 1"
            ),
            {"id": identifier, "start": context_end, "end": marker},
        ).scalar_one_or_none()
        invalid = (
            startup["status"] != "COMPLETE" or interrupted is not None or marker <= context_end
        )
        rows = connection.execute(
            text(
                "SELECT sequence,event,event_hash,committed_at FROM broker_stream_events "
                "WHERE stream_id=:id AND (sequence<=:context OR "
                "(sequence>=:start AND sequence<=:through)) ORDER BY sequence LIMIT :limit"
            ),
            {
                "id": identifier,
                "context": context_end,
                "start": marker,
                "through": source["received"],
                "limit": MAX_EVENTS + 1,
            },
        ).mappings()
        events: list[BrokerEvent] = []
        digest = hashlib.sha256(
            canonical_bytes(
                {
                    "stream_id": str(identifier),
                    "binding_hash": source["binding_hash"],
                    "session_source_hash": startup["source_hash"],
                    "from_sequence": marker,
                }
            )
        )
        expected, size, count = 1, 0, 0
        query_id: str | None = None
        finished: BrokerEvent | None = None
        through = marker - 1
        started_at: str | None = None
        last_at: str | None = None
        failure: str | None = "QUERY_SESSION_INVALID" if invalid else None
        for row in rows:
            event = BrokerEvent.from_dict(row["event"])
            if expected == context_end + 1:
                expected = marker
            encoded = canonical_bytes(event.to_dict())
            if (
                row["sequence"] != expected
                or event.sequence != expected
                or hashlib.sha256(encoded).hexdigest() != row["event_hash"]
            ):
                raise ValueError("receiver query window source is missing or damaged")
            expected += 1
            size += len(encoded)
            count += 1
            if count > MAX_EVENTS or size > MAX_CAPTURE_BYTES - 4096:
                failure = "QUERY_WINDOW_LIMIT"
                break
            if event.sequence <= context_end:
                # Preserve original connection/login/subscription references but
                # never reuse the previous query's account rows or requests.
                if event.callback.startswith("OnRspQry") or (
                    event.callback == "RequestSent"
                    and str((event.data or {}).get("method", "")).startswith("ReqQry")
                ):
                    continue
                events.append(event)
                continue
            through = event.sequence
            if last_at is not None and datetime.fromisoformat(
                event.received_at
            ) < datetime.fromisoformat(last_at):
                failure = "QUERY_RECEIPT_TIME_REGRESSED"
            last_at = event.received_at
            if started_at is None:
                started_at = last_at
            digest.update(
                canonical_bytes(
                    [event.sequence, row["event_hash"], row["committed_at"].isoformat()]
                )
            )
            data = event.data or {}
            if event.sequence == marker:
                query_id = str(UUID(str(data.get("query_id"))))
                if event.callback != "AccountQueryStarted" or event.channel != "TD":
                    raise ValueError("receiver query window start is invalid")
            if event.callback in {"OnRspUserLogin", "OnFrontDisconnected", "OnHeartBeatWarning"}:
                failure = "QUERY_SESSION_INTERRUPTED"
            if event.callback == "AccountQueryFinished" and data.get("query_id") == query_id:
                finished = event
                if event.channel != "TD" or event.error_id:
                    failure = "QUERY_COMPLETION_INVALID"
                if data.get("status") != "COMPLETE":
                    failure = str(data.get("reason") or "QUERY_FAILED")
                break
            events.append(event)
        if finished is None and failure != "QUERY_WINDOW_LIMIT" and through != source["received"]:
            raise ValueError("receiver query window has missing retained callbacks")
    binding = source["binding"]
    assert isinstance(binding, dict)
    reference: dict[str, object] = {
        "stream_id": str(identifier),
        "query_id": query_id,
        "from_sequence": marker,
        "through_sequence": through,
        "started_at": started_at,
        "finished_at": None if finished is None else finished.received_at,
        "source_hash": digest.hexdigest(),
        "session_source_hash": startup["source_hash"],
        "session_through_sequence": context_end,
    }
    projected = project_query(
        {**binding, "query_scope": {}}, _Window(tuple(events), reference, failure)
    )
    result = {
        **reference,
        "status": projected["status"] if finished is not None or failure else "INCOMPLETE",
        "reason": failure or ("FIXED_RECEIVER_QUERY" if finished else "QUERY_NOT_FINISHED"),
        "completeness": projected["completeness"],
        "reconciliation": projected["reconciliation"],
        "execution": {"order_sending": False},
    }
    result["account_observation"] = stream_account_observation(
        binding, result, [event.to_dict() for event in events if event.sequence >= marker]
    )
    return result
