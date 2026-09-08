"""Private incremental account processing behind BrokerLedger's stream Interface.

The checkpoint is rebuildable progress, not another trade book or an assertion
of external coverage. Original callbacks and immutable position entries remain
the facts. No query, connection, strategy replay or sending operation lives here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid5

from sqlalchemy import Connection, text

from northstar_quant import code_revision
from northstar_quant.accounting.ledger import _hash, _time
from northstar_quant.broker.records import BrokerEvent
from northstar_quant.broker.stream_records import read_stream_source

if TYPE_CHECKING:
    from northstar_quant.accounting.ledger import BrokerLedger


def initialize_stream_accounts(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS broker_stream_accounts (
            stream_id uuid PRIMARY KEY REFERENCES broker_streams(stream_id),
            baseline_id uuid NOT NULL REFERENCES broker_account_baselines(baseline_id),
            binding jsonb NOT NULL,
            binding_hash varchar(64) NOT NULL,
            checkpoint jsonb NOT NULL,
            checkpoint_hash varchar(64) NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE OR REPLACE FUNCTION broker_protect_stream_account_binding()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR NEW.stream_id IS DISTINCT FROM OLD.stream_id
                OR NEW.baseline_id IS DISTINCT FROM OLD.baseline_id
                OR NEW.binding IS DISTINCT FROM OLD.binding
                OR NEW.binding_hash IS DISTINCT FROM OLD.binding_hash THEN
                RAISE EXCEPTION 'Stream account binding is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable_binding ON broker_stream_accounts;
        CREATE TRIGGER immutable_binding BEFORE UPDATE OR DELETE ON broker_stream_accounts
            FOR EACH ROW EXECUTE FUNCTION broker_protect_stream_account_binding()
    """)


def _initial(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "through_sequence": 0,
        "source_chain_hash": _hash(binding),
        "last_received_at": None,
        "last_committed_at": None,
        "td_confirmed": False,
        "last_material_sequence": 0,
        "entry_id": None,
        "entry_hash": None,
        "status": "WAITING_FOR_TD_LOGIN",
        "reason": "WAITING_FOR_TD_LOGIN",
    }


def _material(event: BrokerEvent, binding: dict[str, Any]) -> bool:
    if event.callback in {"OnRtnTrade", "OnRtnOrder"}:
        return True
    if event.channel != "TD":
        return False
    if event.error_id or event.callback in {"OnFrontDisconnected", "OnHeartBeatWarning"}:
        return True
    if event.callback == "OnRspUserLogin":
        data = event.data or {}
        return any(
            data.get(field) != binding[key]
            for field, key in (
                ("UserID", "account_id"),
                ("BrokerID", "broker_id"),
                ("TradingDay", "trading_day"),
            )
        )
    return False


def _apply(checkpoint: dict[str, Any], binding: dict[str, Any], item: dict[str, Any]) -> bool:
    event = BrokerEvent.from_dict(item["event"])
    committed = item["committed_at"].astimezone(UTC).isoformat()
    if (
        item["sequence"] != checkpoint["through_sequence"] + 1
        or event.sequence != item["sequence"]
        or _hash(event.to_dict()) != item["event_hash"]
    ):
        raise ValueError("stream account source sequence or digest differs")
    # Application receipt and database commit use distinct clocks. Preserve
    # both; only compare successive readings from the same clock here.
    if checkpoint["last_committed_at"] is not None and _time(committed) < _time(
        checkpoint["last_committed_at"]
    ):
        raise ValueError("stream account source commit chronology differs")
    material = _material(event, binding)
    if event.channel == "TD" and event.callback == "OnRspUserLogin":
        checkpoint["td_confirmed"] = not material
        if material:
            checkpoint.update(status="UNKNOWN", reason="STREAM_TD_IDENTITY_NOT_CONFIRMED")
        elif checkpoint["status"] != "UNKNOWN":
            checkpoint.update(status="READY", reason=None)
    if event.channel == "TD" and (
        event.error_id or event.callback in {"OnFrontDisconnected", "OnHeartBeatWarning"}
    ):
        checkpoint.update(
            td_confirmed=False, status="UNKNOWN", reason="STREAM_ACCOUNT_CONNECTION_ERROR"
        )
    if checkpoint["last_received_at"] is not None and _time(event.received_at) < _time(
        checkpoint["last_received_at"]
    ):
        # Persist the real receipt before blocking the derived account view.
        material = True
        checkpoint.update(status="UNKNOWN", reason="STREAM_ACCOUNT_RECEIPT_TIME_REGRESSED")
    checkpoint.update(
        through_sequence=event.sequence,
        source_chain_hash=_hash(
            [checkpoint["source_chain_hash"], event.sequence, item["event_hash"], committed]
        ),
        last_received_at=event.received_at,
        last_committed_at=committed,
    )
    if material:
        checkpoint["last_material_sequence"] = event.sequence
    return material


class _StreamAccount:
    def __init__(self, ledger: BrokerLedger) -> None:
        self.ledger = ledger
        self.engine = ledger._engine

    @staticmethod
    def _lock(connection: Connection, stream_id: UUID) -> None:
        # Lock order: stream progress -> position book. Query ingestion only
        # reads progress under the position lock, never takes this lock.
        if not isinstance(stream_id, UUID):
            raise ValueError("account progress requires a stream UUID")
        key = int.from_bytes(
            hashlib.sha256(stream_id.bytes + b"account-progress").digest()[:8],
            "big",
            signed=True,
        )
        connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    @staticmethod
    def _read(connection: Connection, stream_id: UUID) -> dict[str, Any]:
        if not isinstance(stream_id, UUID):
            raise ValueError("account progress requires a stream UUID")
        row = (
            connection.execute(
                text("""
                SELECT
                       a.baseline_id, a.binding, a.binding_hash, a.checkpoint, a.checkpoint_hash,
                       b.sha256 AS baseline_hash, latest.entry_id AS account_entry_id,
                       latest.status AS account_status
                FROM (SELECT CAST(:id AS uuid) AS stream_id) selected
                LEFT JOIN broker_stream_accounts a USING (stream_id)
                LEFT JOIN broker_account_baselines b ON b.baseline_id=a.baseline_id
                LEFT JOIN LATERAL (
                    SELECT entry_id, document->>'status' AS status
                    FROM broker_position_entries WHERE baseline_id=a.baseline_id
                    ORDER BY ordinal DESC LIMIT 1
                ) latest ON true
                WHERE selected.stream_id=:id
            """),
                {"id": stream_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("stream not found")
        # Read the immutable source boundary after account progress: a concurrent
        # callback may increase pending work, but a newer checkpoint must never
        # be compared against an older received count and misreported as damage.
        source = read_stream_source(connection, stream_id)
        result = {
            **dict(row),
            "received": source["received"],
            "source_binding_hash": source["binding_hash"],
        }
        if result["binding"] is not None:
            binding, checkpoint = result["binding"], result["checkpoint"]
            if (
                _hash(binding) != result["binding_hash"]
                or _hash(checkpoint) != result["checkpoint_hash"]
                or binding["stream_id"] != str(stream_id)
                or binding["baseline_id"] != str(result["baseline_id"])
                or binding["baseline_hash"] != result["baseline_hash"]
                or binding["source_binding_hash"] != result["source_binding_hash"]
                or not 0 <= checkpoint["through_sequence"] <= result["received"]
            ):
                raise ValueError("stream account binding or checkpoint integrity differs")
        return result

    @staticmethod
    def _view(stream_id: UUID, row: dict[str, Any]) -> dict[str, Any]:
        checkpoint = row["checkpoint"]
        # The checkpoint describes this stream, not permission to ignore newer
        # account facts. Read the latest immutable entry's indexed ordinal/status
        # without replaying the full position book on every market tick.
        account_unknown = row["account_status"] == "UNKNOWN"
        return {
            "stream_id": str(stream_id),
            "baseline_id": None if row["baseline_id"] is None else str(row["baseline_id"]),
            "through_sequence": 0 if checkpoint is None else checkpoint["through_sequence"],
            "last_material_sequence": 0
            if checkpoint is None
            else checkpoint["last_material_sequence"],
            "entry_id": None if checkpoint is None else checkpoint["entry_id"],
            "status": "UNKNOWN"
            if account_unknown
            else "UNBOUND"
            if checkpoint is None
            else checkpoint["status"],
            "reason": "ACCOUNT_LEDGER_UNKNOWN"
            if account_unknown
            else "ACCOUNT_BASELINE_NOT_BOUND"
            if checkpoint is None
            else checkpoint["reason"],
            "account_entry_id": None
            if row["account_entry_id"] is None
            else str(row["account_entry_id"]),
            "pending": row["received"]
            - (0 if checkpoint is None else checkpoint["through_sequence"]),
            "reconciliation": "UNRECONCILED",
            "scope": "SAVED_ASYNC_ACCOUNT_CALLBACKS_ONLY",
            "execution": {"order_sending": False, "cancel_sending": False},
        }

    def progress(self, stream_id: UUID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return self._view(stream_id, self._read(connection, stream_id))

    def bind(self, baseline_id: UUID, stream_id: UUID) -> dict[str, Any]:
        if not isinstance(baseline_id, UUID) or not isinstance(stream_id, UUID):
            raise ValueError("stream account binding requires UUIDs")
        with self.engine.begin() as connection:
            self._lock(connection, stream_id)
            row = self._read(connection, stream_id)
            if row["baseline_id"] is not None:
                if row["baseline_id"] != baseline_id:
                    raise ValueError("stream account is already bound to another baseline")
                return self._view(stream_id, row)
            from northstar_quant.broker.stream_records import read_stream_source

            source = cast(dict[str, Any], read_stream_source(connection, stream_id)["binding"])
            baseline = self.ledger._baselines.get_baseline(baseline_id)
            if (
                source["profile"] != baseline["profile"]
                or source["account_id"] != baseline["account_id"]
            ):
                raise ValueError("stream and baseline require the same account and environment")
            binding = {
                "stream_id": str(stream_id),
                "baseline_id": str(baseline_id),
                "baseline_hash": _hash(baseline),
                "source_binding_hash": row["source_binding_hash"],
                "account_id": baseline["account_id"],
                "broker_id": baseline["profile"]["broker_id"],
                "trading_day": baseline["trading_day"],
                "code_revision": code_revision(),
            }
            checkpoint = _initial(binding)
            connection.execute(
                text("""
                    INSERT INTO broker_stream_accounts
                        (stream_id, baseline_id, binding, binding_hash, checkpoint, checkpoint_hash)
                    VALUES (:id,:baseline,CAST(:binding AS jsonb),:binding_hash,
                            CAST(:checkpoint AS jsonb),:checkpoint_hash)
                """),
                {
                    "id": stream_id,
                    "baseline": baseline_id,
                    "binding": json.dumps(binding),
                    "binding_hash": _hash(binding),
                    "checkpoint": json.dumps(checkpoint),
                    "checkpoint_hash": _hash(checkpoint),
                },
            )
            return self._view(stream_id, self._read(connection, stream_id))

    @staticmethod
    def _events(connection: Connection, stream_id: UUID, after: int, through: int) -> Any:
        return connection.execute(
            text("""
                SELECT sequence, event, event_hash, committed_at FROM broker_stream_events
                WHERE stream_id=:id AND sequence>:after AND sequence<=:through ORDER BY sequence
            """).execution_options(yield_per=100),
            {"id": stream_id, "after": after, "through": through},
        ).mappings()

    def advance(self, stream_id: UUID, through_sequence: int) -> dict[str, Any]:
        if type(through_sequence) is not int or not 0 <= through_sequence <= 100000:
            raise ValueError("account catchup requires a bounded saved sequence")
        with self.engine.begin() as connection:
            self._lock(connection, stream_id)
            row = self._read(connection, stream_id)
            if row["baseline_id"] is None:
                return self._view(stream_id, row)
            if through_sequence > row["received"]:
                raise ValueError("account catchup cannot include unreceived callbacks")
            checkpoint, binding = dict(row["checkpoint"]), row["binding"]
            if through_sequence <= checkpoint["through_sequence"]:
                return self._view(stream_id, row)
            first_material = None
            for item in self._events(
                connection, stream_id, checkpoint["through_sequence"], through_sequence
            ):
                if _apply(checkpoint, binding, dict(item)) and first_material is None:
                    first_material = item["sequence"]
            if checkpoint["through_sequence"] != through_sequence:
                raise ValueError("stream account prefix has missing callbacks")
            if first_material is not None:
                through = checkpoint["last_material_sequence"]
                command = uuid5(stream_id, f"account-prefix:{through}")
                # A prior manual fixed entry or the commit-before-checkpoint
                # crash window is reused; its source range is never rewritten.
                existing = connection.execute(
                    text("""
                        SELECT entry_id FROM broker_position_entries
                        WHERE baseline_id=:baseline
                          AND document->'source_stream'->>'stream_id'=:stream
                          AND (document->'source_stream'->>'through_sequence')::integer>=:through
                          AND (document->'source_stream'->>'through_sequence')::integer<=:target
                        ORDER BY ordinal DESC LIMIT 1
                    """),
                    {
                        "baseline": row["baseline_id"],
                        "stream": str(stream_id),
                        "through": through,
                        "target": through_sequence,
                    },
                ).scalar_one_or_none()
                entry = (
                    self.ledger.get(existing)
                    if existing is not None
                    else self.ledger._ingest(
                        row["baseline_id"],
                        None,
                        stream_id=stream_id,
                        through_sequence=through,
                        request_id=command,
                        account_after_sequence=first_material - 1,
                    )
                )
                checkpoint.update(entry_id=entry["entry_id"], entry_hash=_hash(entry))
                if entry["status"] == "UNKNOWN":
                    checkpoint.update(status="UNKNOWN", reason="ACCOUNT_LEDGER_UNKNOWN")
            connection.execute(
                text("""
                    UPDATE broker_stream_accounts SET checkpoint=CAST(:checkpoint AS jsonb),
                        checkpoint_hash=:hash, updated_at=clock_timestamp() WHERE stream_id=:id
                """),
                {"id": stream_id, "checkpoint": json.dumps(checkpoint), "hash": _hash(checkpoint)},
            )
            return self._view(stream_id, self._read(connection, stream_id))

    def reject_pending_query(self, connection: Connection, baseline_id: UUID) -> None:
        identifiers = list(
            connection.scalars(
                text("SELECT stream_id FROM broker_stream_accounts WHERE baseline_id=:baseline"),
                {"baseline": baseline_id},
            )
        )
        for stream_id in identifiers:
            row = self._read(connection, stream_id)
            checkpoint = dict(row["checkpoint"])
            for item in self._events(
                connection, stream_id, checkpoint["through_sequence"], row["received"]
            ):
                # Exactly the same classification as catch-up, including a
                # receipt-clock regression in an otherwise ordinary MD callback.
                if _apply(checkpoint, row["binding"], dict(item)):
                    raise ValueError(
                        "saved account callbacks require local catchup before query ingestion"
                    )
            if checkpoint["through_sequence"] != row["received"]:
                raise ValueError("stream account prefix has missing callbacks")

    def verify_all(self) -> None:
        with self.engine.connect() as connection:
            identifiers = list(
                connection.scalars(text("SELECT stream_id FROM broker_stream_accounts"))
            )
        for stream_id in identifiers:
            with self.engine.connect() as connection:
                row = self._read(connection, stream_id)
                binding, checkpoint = row["binding"], row["checkpoint"]
                baseline = self.ledger._baselines.get_baseline(row["baseline_id"])
                if _hash(baseline) != binding["baseline_hash"]:
                    raise ValueError("stream account baseline evidence differs")
                rebuilt = _initial(binding)
                for item in self._events(connection, stream_id, 0, checkpoint["through_sequence"]):
                    _apply(rebuilt, binding, dict(item))
                for field in (
                    "through_sequence",
                    "source_chain_hash",
                    "last_received_at",
                    "last_committed_at",
                    "td_confirmed",
                    "last_material_sequence",
                ):
                    if rebuilt[field] != checkpoint[field]:
                        raise ValueError("stream account checkpoint differs from retained source")
                if checkpoint["entry_id"] is not None:
                    entry = self.ledger.get(UUID(checkpoint["entry_id"]))
                    source = entry.get("source_stream", {})
                    if (
                        _hash(entry) != checkpoint["entry_hash"]
                        or entry["baseline_id"] != str(row["baseline_id"])
                        or source.get("stream_id") != str(stream_id)
                        or not checkpoint["last_material_sequence"]
                        <= source.get("through_sequence", -1)
                        <= checkpoint["through_sequence"]
                    ):
                        raise ValueError("stream account checkpoint entry differs")
                    if entry["status"] == "UNKNOWN":
                        rebuilt.update(status="UNKNOWN", reason="ACCOUNT_LEDGER_UNKNOWN")
                elif checkpoint["last_material_sequence"] or checkpoint["entry_hash"] is not None:
                    raise ValueError(
                        "stream account checkpoint has unrecorded account observations"
                    )
                if any(rebuilt[field] != checkpoint[field] for field in ("status", "reason")):
                    raise ValueError("stream account status differs from its fixed facts")
