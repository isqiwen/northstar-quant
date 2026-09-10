"""One explicitly started SimNow reception and shadow-decision loop.

Copied SDK callbacks are the bounded stream's authoritative source in the instance database,
not vendor wire bytes or a published research Snapshot. Each callback commits
before its projection. A stopped/interrupted stream is never restarted; a new
connection requires a new explicit command. There is no execution interface.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import JSON, Connection, Engine, Uuid
from sqlalchemy import text as sql_text
from sqlalchemy.sql.selectable import TextualSelect

from northstar_quant import code_revision
from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker import ctp
from northstar_quant.broker.events import BrokerEvent
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import configured_profile, load_credentials
from northstar_quant.broker.stream_records import append_stream_event, read_stream_archive
from northstar_quant.data_management.broker import resolve_broker_contract, verify_broker_contract
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live.market import advance_market, idle_reason
from northstar_quant.messaging import Endpoint
from northstar_quant.persistence.locks import FileLock
from northstar_quant.persistence.sql import UTCDateTime, write_transaction
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.trading.environment import Environment
from northstar_quant.trading.kernel import KernelState, TradingKernel

ACCEPT_BROKER_EVENT: Endpoint[BrokerEvent, None] = Endpoint("live.broker.receive", BrokerEvent)

_STREAM_LOCK = 728401929
_ACTIVE = {"STARTING", "RECEIVING", "STOP_REQUESTED"}


def text(statement: str) -> TextualSelect:
    """Describe the persisted receipt values returned by this module's SQL."""
    return sql_text(statement).columns(
        binding=JSON,
        state=JSON,
        event=JSON,
        result=JSON,
        checkpoint=JSON,
        stream_id=Uuid,
        query_batch_id=Uuid,
        baseline_id=Uuid,
        account_entry_id=Uuid,
        request_id=Uuid,
        entry_id=Uuid,
        created_at=UTCDateTime(),
        updated_at=UTCDateTime(),
        committed_at=UTCDateTime(),
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


def initialize_streams(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        for statement in """
        CREATE TABLE IF NOT EXISTS broker_streams (
            stream_id CHAR(32) PRIMARY KEY,
            query_batch_id CHAR(32) NOT NULL REFERENCES broker_query_batches(batch_id),
            configuration_id varchar(64) NOT NULL REFERENCES paper_configurations(configuration_id),
            binding JSON NOT NULL, binding_hash varchar(64) NOT NULL,
            status varchar(24) NOT NULL, paused boolean NOT NULL,
            reason varchar(96) NOT NULL, received integer NOT NULL DEFAULT 0,
            cursor integer NOT NULL DEFAULT 0, byte_count bigint NOT NULL DEFAULT 0,
            state JSON NOT NULL, state_hash varchar(64) NOT NULL,
            created_at TEXT NOT NULL DEFAULT (clock_timestamp()),
            updated_at TEXT NOT NULL DEFAULT (clock_timestamp()),
            CHECK (cursor >= 0 AND cursor <= received AND received <= 100000)
        );
        CREATE TABLE IF NOT EXISTS broker_stream_events (
            stream_id CHAR(32) REFERENCES broker_streams(stream_id), sequence integer,
            event JSON NOT NULL, event_hash varchar(64) NOT NULL,
            committed_at TEXT NOT NULL DEFAULT (clock_timestamp()),
            PRIMARY KEY (stream_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS broker_stream_steps (
            stream_id CHAR(32) REFERENCES broker_streams(stream_id), sequence integer,
            result JSON NOT NULL, result_hash varchar(64) NOT NULL,
            committed_at TEXT NOT NULL DEFAULT (clock_timestamp()),
            PRIMARY KEY (stream_id, sequence),
            FOREIGN KEY (stream_id, sequence) REFERENCES broker_stream_events(stream_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS broker_stream_commands (
            request_id CHAR(32) PRIMARY KEY,
            stream_id CHAR(32) NOT NULL REFERENCES broker_streams(stream_id),
            action varchar(12) NOT NULL, result JSON NOT NULL,
            committed_at TEXT NOT NULL DEFAULT (clock_timestamp())
        );
""".split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        changed = " OR ".join(
            f"OLD.{field} IS NOT NEW.{field}"
            for field in (
                "stream_id",
                "query_batch_id",
                "configuration_id",
                "binding",
                "binding_hash",
                "created_at",
            )
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS stream_binding_update BEFORE UPDATE ON broker_streams "
            f"WHEN {changed} BEGIN SELECT RAISE(ABORT, 'Stream binding is immutable'); END"
        )
        for table in ("broker_stream_events", "broker_stream_steps", "broker_stream_commands"):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                    f"BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Stream facts are immutable'); END"
                )
        return
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS broker_streams (
            stream_id uuid PRIMARY KEY,
            query_batch_id uuid NOT NULL REFERENCES broker_query_batches(batch_id),
            configuration_id varchar(64) NOT NULL REFERENCES paper_configurations(configuration_id),
            binding jsonb NOT NULL, binding_hash varchar(64) NOT NULL,
            status varchar(24) NOT NULL, paused boolean NOT NULL,
            reason varchar(96) NOT NULL, received integer NOT NULL DEFAULT 0,
            cursor integer NOT NULL DEFAULT 0, byte_count bigint NOT NULL DEFAULT 0,
            state jsonb NOT NULL, state_hash varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK (cursor >= 0 AND cursor <= received AND received <= 100000)
        );
        CREATE TABLE IF NOT EXISTS broker_stream_events (
            stream_id uuid REFERENCES broker_streams(stream_id), sequence integer,
            event jsonb NOT NULL, event_hash varchar(64) NOT NULL,
            committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (stream_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS broker_stream_steps (
            stream_id uuid REFERENCES broker_streams(stream_id), sequence integer,
            result jsonb NOT NULL, result_hash varchar(64) NOT NULL,
            committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (stream_id, sequence),
            FOREIGN KEY (stream_id, sequence) REFERENCES broker_stream_events(stream_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS broker_stream_commands (
            request_id uuid PRIMARY KEY,
            stream_id uuid NOT NULL REFERENCES broker_streams(stream_id),
            action varchar(12) NOT NULL, result jsonb NOT NULL,
            committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE OR REPLACE FUNCTION stream_preserve_binding() RETURNS trigger AS $$
        BEGIN
            IF (NEW.stream_id, NEW.query_batch_id, NEW.configuration_id, NEW.binding,
                NEW.binding_hash, NEW.created_at) IS DISTINCT FROM
               (OLD.stream_id, OLD.query_batch_id, OLD.configuration_id, OLD.binding,
                OLD.binding_hash, OLD.created_at) THEN
                RAISE EXCEPTION 'Stream bindings are immutable';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable_binding ON broker_streams;
        CREATE TRIGGER immutable_binding BEFORE UPDATE ON broker_streams
            FOR EACH ROW EXECUTE FUNCTION stream_preserve_binding();
    """)
    for table in ("broker_stream_events", "broker_stream_steps", "broker_stream_commands"):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable ON {table}")
        connection.exec_driver_sql(
            f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION paper_reject_fact_change()"
        )


class LiveStreams:
    """Own explicit start, durable reception, shadow pause and terminal stop."""

    def __init__(
        self,
        engine: Engine,
        library: DataLibrary,
        *,
        check_ownership: Callable[[], None] | None = None,
    ) -> None:
        self._engine = engine
        self._library = library
        self._configurations = ConfigurationStore(engine)
        self._ledger = BrokerLedger(engine)
        self._check_ownership = check_ownership
        self._guard = threading.Lock()
        self._workers: dict[UUID, tuple[threading.Thread, threading.Event]] = {}

    def start(
        self,
        query_batch_id: UUID,
        configuration_id: str,
        *,
        request_id: UUID,
        duration_seconds: int,
        allow_retention: bool,
        use_basis: str,
    ) -> dict[str, object]:
        if type(duration_seconds) is not int or not 60 <= duration_seconds <= 7200:
            raise ValueError("stream duration must be 60..7200 seconds")
        if (
            allow_retention is not True
            or not isinstance(use_basis, str)
            or not 1 <= len(use_basis.strip()) <= 500
        ):
            raise ValueError("stream needs explicit retention permission and a bounded use basis")
        request = {
            "query_batch_id": str(query_batch_id),
            "configuration_id": configuration_id,
            "duration_seconds": duration_seconds,
            "allow_retention": True,
            "use_basis": use_basis.strip(),
        }
        with self._guard:
            self._workers = {
                key: value for key, value in self._workers.items() if value[0].is_alive()
            }
            try:
                existing = self.get(request_id)
            except LookupError:
                pass
            else:
                if _object(existing["binding"])["request"] != request:
                    raise ValueError("stream request identity is bound to different input")
                return existing
            if self._check_ownership is not None:
                self._check_ownership()
            query = BrokerRecords(self._engine).get(query_batch_id)
            completeness = _object(query["completeness"])
            if query["status"] != "COMPLETE" or completeness["identity"] != "CONFIRMED":
                raise ValueError("stream requires a complete identity-confirmed saved query")
            rows = _object(_object(completeness["sections"])["instrument"])["rows"]
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError("stream requires one exact observed instrument")
            terms = _object(rows[0])
            contract = resolve_broker_contract(self._engine, terms)
            configuration = self._configurations.get_configuration(configuration_id)
            profile = configured_profile(str(_object(query["profile"])["name"]))
            credentials = load_credentials()
            if credentials.user_id != query["account_id"]:
                raise ValueError("configured SimNow account differs from the selected query")
            if not ctp.sdk_status()["available"]:
                raise ValueError(
                    "continuous SimNow reception requires the verified Linux amd64 SDK"
                )
            binding = {
                "request": request,
                "environment": Environment.SANDBOX.value,
                "profile": profile.identity(),
                "account_id": query["account_id"],
                "instrument": query["instrument"],
                "contract_id": str(contract.contract_id),
                "terms": terms,
                "configuration": configuration,
                "code_revision": code_revision(),
                "mode": "SHADOW_ONLY",
                "source_kind": "COPIED_CTP_CALLBACKS",
                "scope": "SHFE_DAY_OBSERVED_MINUTES",
                "order_sending": False,
            }
            # Session locks are released explicitly before returning this pooled
            # connection. One receiver per database; bounded queries share the
            # existing account lock, so they cannot overlap this connection.
            owner = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            scope = f"northstar-simnow-query:{profile.name}:{credentials.user_id}"
            account_key = int.from_bytes(
                hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True
            )
            locked: list[int] = []
            created = False
            try:
                for key in (_STREAM_LOCK, account_key):
                    if owner.dialect.name == "sqlite":
                        path = Path(str(self._engine.url.database) + f".{key}")
                        try:
                            lock = FileLock(path)
                        except BlockingIOError as exc:
                            raise ValueError(
                                "a SimNow receiver or account query is already running"
                            ) from exc
                        owner.info.setdefault("live_locks", []).append(lock)
                        continue
                    if not owner.execute(
                        text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                    ).scalar_one():
                        raise ValueError("a SimNow receiver or account query is already running")
                    locked.append(key)
                with write_transaction(self._engine) as connection:
                    connection.execute(
                        text("""
                        INSERT INTO broker_streams
                        (stream_id, query_batch_id, configuration_id, binding, binding_hash,
                         status, paused, reason, state, state_hash)
                        VALUES (:id, :query, :config, :binding, :hash,
                                'STARTING', false, 'CONNECTING', '{}', :state_hash)
                    """),
                        {
                            "id": request_id,
                            "query": query_batch_id,
                            "config": configuration_id,
                            "binding": _json(binding),
                            "hash": _hash(binding),
                            "state_hash": _hash({}),
                        },
                    )
                created = True
                baseline = BrokerBaselines(self._engine).context(query_batch_id)["baseline"]
                if baseline is not None:
                    # Fix the account before reception, never select a newer
                    # baseline from inside a callback or change the saved binding.
                    self._ledger.bind_stream(UUID(baseline["baseline_id"]), request_id)
                stopped = threading.Event()
                worker = threading.Thread(
                    target=self._run,
                    args=(
                        request_id,
                        binding,
                        credentials,
                        owner,
                        locked,
                        stopped,
                        Environment.SANDBOX,
                    ),
                    name="northstar-simnow-shadow",
                    daemon=True,
                )
                self._workers[request_id] = (worker, stopped)
                worker.start()
            except BaseException:
                if created:
                    try:
                        self._terminal(request_id, "FAILED", "START_FAILED", paused=True)
                    except Exception:
                        pass
                self._unlock(owner, locked)
                raise
        return self.get(request_id)

    @staticmethod
    def _unlock(owner: Connection, keys: list[int]) -> None:
        try:
            for held in owner.info.pop("live_locks", []):
                held.close()
            for key in reversed(keys):
                owner.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        except Exception:
            owner.invalidate()
        finally:
            owner.close()

    def _run(
        self,
        identifier: UUID,
        binding: dict[str, object],
        credentials: object,
        owner: Connection,
        locks: list[int],
        stopped: threading.Event,
        environment: Environment,
    ) -> None:
        from northstar_quant.broker.settings import Credentials

        failure: str | None = None
        # The SDK channel enters one owner thread; faults require local recovery.
        kernel = TradingKernel(
            ACCEPT_BROKER_EVENT,
            lambda event: self.accept(identifier, event),
            environment=environment,
        )
        kernel.start()
        try:
            pid_query = "SELECT 1" if owner.dialect.name == "sqlite" else "SELECT pg_backend_pid()"
            owner_pid = owner.execute(text(pid_query)).scalar_one()
            database_path = (
                Path(str(self._engine.url.database)) if owner.dialect.name == "sqlite" else None
            )
            database_identity = database_path.stat() if database_path else None
            last_check = 0.0

            def should_stop() -> bool:
                nonlocal last_check
                if kernel.status.state == KernelState.FAULTED:
                    raise RuntimeError("receiver kernel faulted")
                if stopped.is_set():
                    return True
                if time.monotonic() - last_check < 0.5:
                    return False
                # HTTP requests are not the receiver's lifecycle monitor. Check
                # instance/account ownership even when no browser is connected.
                # These bounded filesystem checks run on the existing poll, not
                # on every tick. They grant no execution or failover authority.
                if self._check_ownership is not None:
                    self._check_ownership()
                for held in owner.info.get("live_locks", []):
                    held.check()
                if database_path is not None and database_identity is not None:
                    observed = database_path.stat()
                    if (observed.st_dev, observed.st_ino) != (
                        database_identity.st_dev,
                        database_identity.st_ino,
                    ):
                        raise ValueError("receiver database file was replaced")
                if owner.invalidated or owner.execute(text(pid_query)).scalar_one() != owner_pid:
                    raise ValueError("receiver ownership connection was lost")
                last_check = time.monotonic()
                return self._poll(identifier, stopped)

            with write_transaction(self._engine) as connection:
                connection.execute(
                    text("""
                    UPDATE broker_streams SET status='RECEIVING', updated_at=clock_timestamp()
                    WHERE stream_id=:id AND status='STARTING'
                """),
                    {"id": identifier},
                )
            if self._poll(identifier, stopped):
                return
            failure = ctp.stream_account(
                configured_profile(str(_object(binding["profile"])["name"])),
                cast(Credentials, credentials),
                str(binding["instrument"]),
                on_event=kernel.advance,
                should_stop=should_stop,
                duration_seconds=cast(int, _object(binding["request"])["duration_seconds"]),
            )
        except Exception:
            failure = "RECEPTION_OR_PERSISTENCE_FAILED"
        finally:
            if kernel.status.state == KernelState.FAULTED:
                failure = "RECEPTION_OR_PERSISTENCE_FAILED"
            kernel.close()
            try:
                self._terminal(
                    identifier,
                    "FAILED" if failure else "STOPPED",
                    failure or "CONNECTION_ENDED",
                    paused=True,
                )
            except Exception:
                # A failed database may leave interrupted evidence. Never log
                # vendor objects or retry an external action from this thread.
                pass
            finally:
                self._unlock(owner, locks)

    def _terminal(self, identifier: UUID, status: str, reason: str, *, paused: bool) -> None:
        with write_transaction(self._engine) as connection:
            connection.execute(
                text("""
                UPDATE broker_streams SET status=:status, reason=:reason, paused=:paused,
                    updated_at=clock_timestamp() WHERE stream_id=:id
            """),
                {"id": identifier, "status": status, "reason": reason, "paused": paused},
            )

    def _poll(self, identifier: UUID, stopped: threading.Event) -> bool:
        if stopped.is_set():
            return True
        with write_transaction(self._engine) as connection:
            self._timeouts(connection)
            row = self._row(connection, identifier, lock=True)
            if row["status"] == "STOP_REQUESTED":
                return True
            state = _object(row["state"])
            if not row["paused"]:
                reason = idle_reason(_object(state.get("market", {})), now=datetime.now(UTC))
                if reason is not None and reason != row["reason"]:
                    if reason != "SCHEDULED_BREAK":
                        state["last_pause_reason"] = reason
                        state["last_pause_at"] = datetime.now(UTC).isoformat()
                    connection.execute(
                        text("""
                        UPDATE broker_streams SET paused=:paused, reason=:reason,
                        state=:state, state_hash=:hash,
                        updated_at=clock_timestamp() WHERE stream_id=:id
                    """),
                        {
                            "id": identifier,
                            "reason": reason,
                            "paused": reason != "SCHEDULED_BREAK",
                            "state": _json(state),
                            "hash": _hash(state),
                        },
                    )
        return False

    @staticmethod
    def _timeouts(connection: Connection) -> None:
        if connection.dialect.name == "sqlite":
            return
        connection.exec_driver_sql("SET LOCAL statement_timeout = '2s'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '500ms'")

    @staticmethod
    def _row(connection: Connection, identifier: UUID, *, lock: bool = False) -> dict[str, object]:
        row = (
            connection.execute(
                text(
                    "SELECT * FROM broker_streams WHERE stream_id=:id"
                    + (" FOR UPDATE" if lock and connection.dialect.name == "postgresql" else "")
                ),
                {"id": identifier},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LookupError("stream not found")
        result = dict(row)
        if (
            _hash(result["binding"]) != result["binding_hash"]
            or _hash(result["state"]) != result["state_hash"]
        ):
            raise ValueError("stream binding or checkpoint integrity failed")
        return result

    def accept(self, identifier: UUID, event: BrokerEvent) -> None:
        """Persist actual copied content before processing; retries never advance twice."""
        encoded = event.to_dict()
        with write_transaction(self._engine) as connection:
            self._timeouts(connection)
            row = self._row(connection, identifier, lock=True)
            append_stream_event(
                connection,
                identifier,
                event,
                receiving=row["status"] in {"RECEIVING", "STOP_REQUESTED"},
            )
        # Durable reception, account application and shadow calculation have
        # distinct commits. A failed application leaves the source for explicit
        # local catch-up. Pausing shadow never prevents booking actual fills.
        progress = self._ledger.advance_stream(identifier, event.sequence)
        with write_transaction(self._engine) as connection:
            self._timeouts(connection)
            row = self._row(connection, identifier, lock=True)
            if event.sequence <= cast(int, row["cursor"]):
                return
            if event.sequence != cast(int, row["cursor"]) + 1:
                raise ValueError("stream projection cannot skip a committed input")
            binding, state = _object(row["binding"]), dict(_object(row["state"]))
            data = dict(event.data or {})
            reason: str | None = None
            if progress["status"] == "UNKNOWN":
                reason = "ACCOUNT_REPLIES_UNKNOWN"
            if event.callback == "OnRspUserLogin" and not event.error_id:
                account, broker_id = binding["account_id"], _object(binding["profile"])["broker_id"]
                if (
                    event.channel == "TD"
                    and (data.get("UserID") != account or data.get("BrokerID") != broker_id)
                ) or (
                    event.channel == "MD"
                    and (
                        data.get("UserID") not in {None, "", account}
                        or data.get("BrokerID") not in {None, "", broker_id}
                    )
                ):
                    reason = "ACCOUNT_IDENTITY_MISMATCH"
                else:
                    state[event.channel + "_trading_day"] = data.get("TradingDay")
            if event.error_id or event.callback in {"OnFrontDisconnected", "OnHeartBeatWarning"}:
                reason = "CONNECTION_OR_CALLBACK_ERROR"
            if event.callback in {"OnRtnOrder", "OnRtnTrade"} and (
                data.get("InvestorID") != binding["account_id"]
                or data.get("BrokerID") != _object(binding["profile"])["broker_id"]
            ):
                reason = "ACCOUNT_CALLBACK_IDENTITY_MISMATCH"
            if (
                event.callback == "OnRspQryInstrument"
                and data.get("InstrumentID") == binding["instrument"]
            ):
                terms = _object(binding["terms"])
                if any(
                    data.get(key) != terms.get(key)
                    for key in (
                        "ExchangeID",
                        "ProductClass",
                        "ProductID",
                        "PriceTick",
                        "VolumeMultiple",
                        "DeliveryYear",
                        "DeliveryMonth",
                        "ExpireDate",
                    )
                ):
                    reason = "CONTRACT_TERMS_CHANGED"
            result: dict[str, object] = {"event_hash": _hash(encoded), "bar": None, "intent": None}
            if reason is not None and reason != "ACCOUNT_REPLIES_UNKNOWN":
                state["connection_error"] = reason
            if event.callback == "OnRtnDepthMarketData" and not row["paused"] and reason is None:
                day = state.get("TD_trading_day")
                if not day or day != state.get("MD_trading_day") or day != data.get("TradingDay"):
                    reason = "TRADING_DAY_NOT_CONFIRMED"
                else:
                    market = advance_market(
                        _object(state.get("market", {})),
                        event,
                        instrument=str(binding["instrument"]),
                        contract_id=UUID(str(binding["contract_id"])),
                        price_tick=Decimal(str(_object(binding["terms"])["PriceTick"])),
                        config=ResearchConfig.from_mapping(
                            _object(_object(binding["configuration"])["config"])
                        ).strategy,
                        now=datetime.now(UTC),
                    )
                    state["market"] = market
                    result.update(bar=market.get("completed_bar"), intent=market.get("intent"))
                    if market["status"] == "HALTED":
                        reason = str(market["reason"])
            state["last_received_at"] = event.received_at
            if event.callback == "OnRtnDepthMarketData":
                state["last_market_received_at"] = event.received_at
                state["last_market_data"] = data
            if reason is not None:
                state["last_pause_reason"] = reason
                state["last_pause_at"] = datetime.now(UTC).isoformat()
            result["reason"] = reason or (row["reason"] if row["paused"] else None)
            connection.execute(
                text("""
                INSERT INTO broker_stream_steps(stream_id, sequence, result, result_hash)
                VALUES (:id, :seq, :result, :hash)
            """),
                {
                    "id": identifier,
                    "seq": event.sequence,
                    "result": _json(result),
                    "hash": _hash(result),
                },
            )
            connection.execute(
                text("""
                UPDATE broker_streams SET cursor=:seq, state=:state,
                    state_hash=:hash,
                    paused=:paused, reason=:reason, updated_at=clock_timestamp() WHERE stream_id=:id
            """),
                {
                    "id": identifier,
                    "seq": event.sequence,
                    "state": _json(state),
                    "hash": _hash(state),
                    "paused": bool(row["paused"] or reason),
                    "reason": reason
                    or (
                        row["reason"]
                        if row["paused"]
                        else str(
                            _object(state.get("market", {})).get("reason", "WAITING_FOR_MARKET")
                        )
                    ),
                },
            )

    def control(self, identifier: UUID, action: str, *, request_id: UUID) -> dict[str, object]:
        if action not in {"PAUSE", "RESUME", "STOP"}:
            raise ValueError("stream control must be PAUSE, RESUME or STOP")
        with write_transaction(self._engine) as connection:
            self._timeouts(connection)
            row = self._row(connection, identifier, lock=True)
            previous = (
                connection.execute(
                    text("SELECT * FROM broker_stream_commands WHERE request_id=:id"),
                    {"id": request_id},
                )
                .mappings()
                .first()
            )
            if previous is not None:
                if previous["stream_id"] != identifier or previous["action"] != action:
                    raise ValueError("stream command identity is bound to different input")
                return _object(previous["result"])
            if action == "RESUME" and (
                row["status"] != "RECEIVING" or not self._attached(identifier)
            ):
                raise ValueError(
                    "resume needs this process's receiving stream; stopped streams never reconnect"
                )
            state = dict(_object(row["state"]))
            if action == "RESUME":
                if state.get("connection_error"):
                    raise ValueError("connection or identity error requires a new verified stream")
                progress = self._ledger.stream_progress(identifier)
                if progress["status"] != "UNBOUND" and (
                    progress["status"] != "READY"
                    or cast(int, progress["through_sequence"]) < cast(int, row["received"])
                ):
                    raise ValueError("resume requires caught-up, known saved account replies")
                state.pop("market", None)
            status = (
                "STOP_REQUESTED" if action == "STOP" and row["status"] in _ACTIVE else row["status"]
            )
            reason = "OPERATOR_" + action
            result = {
                "stream_id": str(identifier),
                "request_id": str(request_id),
                "action": action,
                "status": status,
                "paused": action != "RESUME",
                "order_sending": False,
            }
            connection.execute(
                text("""
                UPDATE broker_streams SET status=:status, paused=:paused, reason=:reason,
                    state=:state, state_hash=:hash, updated_at=clock_timestamp()
                WHERE stream_id=:id
            """),
                {
                    "id": identifier,
                    "status": status,
                    "paused": action != "RESUME",
                    "reason": reason,
                    "state": _json(state),
                    "hash": _hash(state),
                },
            )
            connection.execute(
                text("""
                INSERT INTO broker_stream_commands(request_id, stream_id, action, result)
                VALUES (:request, :id, :action, :result)
            """),
                {
                    "request": request_id,
                    "id": identifier,
                    "action": action,
                    "result": _json(result),
                },
            )
        return result

    def _attached(self, identifier: UUID) -> bool:
        worker = self._workers.get(identifier)
        return worker is not None and worker[0].is_alive()

    def catchup_account(
        self, identifier: UUID, baseline_id: UUID, through_sequence: int
    ) -> dict[str, object]:
        """Apply only retained account replies; never reconnect or replay decisions.

        The first explicit catch-up may fix the account for an unbound stream.
        Subsequent calls cannot replace that baseline. The requested upper bound
        is never expanded to a newer received tail.
        """
        if type(through_sequence) is not int or not 1 <= through_sequence <= 100000:
            raise ValueError("account catch-up requires a bounded retained prefix")
        with self._engine.connect() as connection:
            row = self._row(connection, identifier)
            if through_sequence > cast(int, row["received"]):
                raise ValueError("account catch-up cannot include unreceived callbacks")
        self._ledger.bind_stream(baseline_id, identifier)
        return self._ledger.advance_stream(identifier, through_sequence)

    def archive(
        self,
        identifier: UUID,
        *,
        through_sequence: int,
        session_open: str,
        session_close: str,
        request_id: UUID,
        allow_download: bool = False,
    ) -> dict[str, object]:
        """Publish an explicitly selected captured range through Data, not the SDK.

        This is local reconstruction for research, never resumed shadow decisions
        or execution. Data owns permission, idempotency, processing and publication;
        an admitted but invalid range returns its retained FAILED attempt.
        """
        content = read_stream_archive(self._engine, identifier, through_sequence)
        return self._library.receive_stream(
            content,
            session_open=session_open,
            session_close=session_close,
            allow_download=allow_download,
            request_id=str(request_id),
        )

    def get(self, identifier: UUID) -> dict[str, object]:
        with self._engine.connect() as connection:
            row = self._row(connection, identifier)
            steps = (
                connection.execute(
                    text("""
                SELECT sequence, result, result_hash, committed_at FROM broker_stream_steps
                WHERE stream_id=:id AND sequence<=:cursor AND
                    (result->'intent' <> 'null' OR result->'bar' <> 'null')
                ORDER BY sequence DESC LIMIT 20
            """),
                    {"id": identifier, "cursor": row["cursor"]},
                )
                .mappings()
                .all()
            )
        attached = self._attached(identifier)
        if any(_hash(item["result"]) != item["result_hash"] for item in steps):
            raise ValueError("stream decision integrity failed")
        state = _object(row["state"])
        account_progress = self._ledger.stream_progress(identifier)
        last = state.get("last_market_received_at")
        age = (
            None
            if last is None
            else (datetime.now(UTC) - datetime.fromisoformat(str(last))).total_seconds()
        )
        return {
            "stream_id": str(identifier),
            "binding": row["binding"],
            "status": row["status"],
            "connection": "RECEIVING"
            if attached and row["status"] == "RECEIVING"
            else "NOT_ATTACHED",
            "paused": bool(
                row["paused"]
                or not attached
                or age is None
                or age > 5
                or age < -1
                or account_progress["status"] == "UNKNOWN"
                or (
                    account_progress["status"] != "UNBOUND"
                    and cast(int, account_progress["through_sequence"]) < cast(int, row["received"])
                )
            ),
            "reason": "OWNER_NOT_ATTACHED"
            if not attached and row["status"] in _ACTIVE
            else row["reason"],
            "received": row["received"],
            "cursor": row["cursor"],
            "byte_count": row["byte_count"],
            "state": state,
            "account_progress": account_progress,
            "market_age_seconds": age,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "order_sending": False,
            "cancel_sending": False,
            "archives": self._library.stream_attempts(identifier),
            "steps": [
                {
                    "sequence": item["sequence"],
                    "result": item["result"],
                    "committed_at": str(item["committed_at"]),
                }
                for item in steps
            ],
        }

    def decision(self, identifier: UUID, sequence: int) -> dict[str, object]:
        """Read one fixed shadow decision and its confirming source, never the latest state.

        This works beyond the workspace's recent-step window. Reading or using a
        historical target cannot reconnect, replay it, or give it trading authority.
        """
        if type(sequence) is not int or not 1 <= sequence <= 100_000:
            raise ValueError("decision sequence must identify one retained callback")
        with self._engine.connect() as connection:
            stream = self._row(connection, identifier)
            row = (
                connection.execute(
                    text("""
                    SELECT e.event, e.event_hash, e.committed_at AS event_committed_at,
                           s.result, s.result_hash, s.committed_at AS step_committed_at
                    FROM broker_stream_events e JOIN broker_stream_steps s
                        USING (stream_id, sequence)
                    WHERE e.stream_id=:id AND e.sequence=:sequence
                """),
                    {"id": identifier, "sequence": sequence},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or sequence > cast(int, stream["cursor"]):
            raise LookupError("committed shadow decision not found")
        if (
            _hash(row["event"]) != row["event_hash"]
            or _hash(row["result"]) != row["result_hash"]
            or row["result"]["event_hash"] != row["event_hash"]
        ):
            raise ValueError("shadow decision source or result integrity failed")
        binding = _object(stream["binding"])
        query = BrokerRecords(self._engine).get(
            UUID(str(_object(binding["request"])["query_batch_id"]))
        )
        if any(query[key] != binding[key] for key in ("profile", "account_id", "instrument")):
            raise ValueError("shadow decision source identity differs from its binding")
        configuration = self._configurations.get_configuration(str(stream["configuration_id"]))
        if configuration != binding["configuration"]:
            raise ValueError("shadow decision configuration differs from its fixed revision")
        verify_broker_contract(
            self._engine, UUID(str(binding["contract_id"])), _object(binding["terms"])
        )
        return {
            "stream_id": str(identifier),
            "sequence": sequence,
            "binding": binding,
            "event": row["event"],
            "event_committed_at": row["event_committed_at"].isoformat(),
            "result": row["result"],
            "step_committed_at": row["step_committed_at"].isoformat(),
        }

    def events(self, identifier: UUID, *, after: int = 0) -> list[dict[str, object]]:
        if type(after) is not int or after < 0:
            raise ValueError("event cursor must be nonnegative")
        with self._engine.connect() as connection:
            self._row(connection, identifier)
            rows = (
                connection.execute(
                    text("""
                SELECT event, event_hash, committed_at FROM broker_stream_events
                WHERE stream_id=:id AND sequence>:after ORDER BY sequence LIMIT 100
            """),
                    {"id": identifier, "after": after},
                )
                .mappings()
                .all()
            )
        for row in rows:
            if _hash(row["event"]) != row["event_hash"]:
                raise ValueError("stream source integrity failed")
        return [{"event": row["event"], "committed_at": str(row["committed_at"])} for row in rows]

    def list(self) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            identifiers = (
                connection.execute(
                    text("SELECT stream_id FROM broker_streams ORDER BY created_at DESC LIMIT 50")
                )
                .scalars()
                .all()
            )
        return [self.get(identifier) for identifier in identifiers]

    def close(self) -> None:
        """Shutdown only connections owned here; no startup recovery or external retry."""
        for _, stop in tuple(self._workers.values()):
            stop.set()
        for worker, _ in tuple(self._workers.values()):
            worker.join(timeout=8)
        if any(worker.is_alive() for worker, _ in tuple(self._workers.values())):
            raise RuntimeError("Live receiver has not stopped; account ownership retained")

    def verify_all(self) -> int:
        """Verify restored source/step chains; never activate a recovered receiver."""
        count = 0
        with self._engine.connect() as connection:
            identifiers = (
                connection.execute(text("SELECT stream_id FROM broker_streams")).scalars().all()
            )
        for identifier in identifiers:
            with self._engine.connect() as connection:
                row = self._row(connection, identifier)
                binding = _object(row["binding"])
                BrokerRecords(self._engine).get(
                    UUID(str(_object(binding["request"])["query_batch_id"]))
                )
                config = self._configurations.get_configuration(str(row["configuration_id"]))
                if config != binding["configuration"]:
                    raise ValueError("stream configuration differs from its fixed revision")
                verify_broker_contract(
                    self._engine, UUID(str(binding["contract_id"])), _object(binding["terms"])
                )
                records = (
                    connection.execution_options(yield_per=100)
                    .execute(
                        text("""
                    SELECT e.sequence, e.event, e.event_hash, s.result, s.result_hash
                    FROM broker_stream_events e LEFT JOIN broker_stream_steps s
                        USING (stream_id, sequence)
                    WHERE e.stream_id=:id ORDER BY sequence
                """),
                        {"id": identifier},
                    )
                    .mappings()
                )
                received = projected = 0
                for item in records:
                    received += 1
                    if item["sequence"] != received or _hash(item["event"]) != item["event_hash"]:
                        raise ValueError("stream source sequence or digest differs")
                    if item["result"] is not None:
                        projected += 1
                        if (
                            projected != received
                            or _hash(item["result"]) != item["result_hash"]
                            or item["result"]["event_hash"] != item["event_hash"]
                        ):
                            raise ValueError("stream projection sequence or digest differs")
                if received != row["received"] or projected != row["cursor"]:
                    raise ValueError("stream committed cursors differ from retained evidence")
            count += 1
        return count
