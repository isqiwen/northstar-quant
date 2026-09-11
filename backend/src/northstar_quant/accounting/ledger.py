"""Append confirmed trades, then compare frozen gross positions independently.

One bounded, same-day position ledger starts at an existing flat observation.
Entries store only newly identified fills; previous entries, raw queries and
fixed copied stream prefixes are retained by hash. Cash, fees, settlement and
sending authority are not inferred.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from northstar_quant import code_revision
from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.positions import PositionChange, project_intraday_positions
from northstar_quant.broker.account_reports import (
    decode_trade,
    position_observations,
    query_trades,
    stream_trades,
)
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.data_management.broker import resolve_broker_contract, verify_broker_contract
from northstar_quant.persistence.sql import UTCDateTime, write_transaction

_metadata = MetaData()
_entries = Table(
    "broker_position_entries",
    _metadata,
    Column("entry_id", Uuid(as_uuid=True), primary_key=True),
    Column("baseline_id", Uuid(as_uuid=True), nullable=False),
    Column("source_batch_id", Uuid(as_uuid=True), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("recorded_at", UTCDateTime(), nullable=False),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("baseline_id", "ordinal"),
)
_checks = Table(
    "broker_position_checks",
    _metadata,
    Column("check_id", Uuid(as_uuid=True), primary_key=True),
    Column("entry_id", Uuid(as_uuid=True), nullable=False),
    Column("query_batch_id", Uuid(as_uuid=True), nullable=False),
    Column("recorded_at", UTCDateTime(), nullable=False),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("entry_id", "query_batch_id"),
)
_EXECUTION = {"order_sending": False, "cancel_sending": False}
_MAX_ENTRIES = 1000
_MAX_FILLS = 10000


def initialize_broker_ledger(connection: Connection) -> None:
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        for table_name in ("broker_position_entries", "broker_position_checks"):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table_name}_{action} "
                    f"BEFORE {action} ON {table_name} "
                    "BEGIN SELECT RAISE(ABORT, 'Confirmed facts are immutable'); END"
                )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS broker_position_query_source "
            "ON broker_position_entries(baseline_id, source_batch_id) "
            "WHERE json_type(document, '$.source_stream') IS NULL"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS broker_position_stream_source "
            "ON broker_position_entries(baseline_id, "
            "json_extract(document, '$.source_stream.stream_id'), "
            "json_extract(document, '$.source_stream.through_sequence')) "
            "WHERE json_type(document, '$.source_stream') IS NOT NULL"
        )
        return
    # Queries and copied stream prefixes are distinct current source kinds. This
    # changes uniqueness only; retained documents, amounts and hashes stay intact.
    connection.exec_driver_sql("""
        ALTER TABLE broker_position_entries DROP CONSTRAINT IF EXISTS
            broker_position_entries_baseline_id_source_batch_id_key;
        CREATE UNIQUE INDEX IF NOT EXISTS broker_position_query_source
            ON broker_position_entries(baseline_id, source_batch_id)
            WHERE NOT (document ? 'source_stream');
        CREATE UNIQUE INDEX IF NOT EXISTS broker_position_stream_source
            ON broker_position_entries(baseline_id,
                (document->'source_stream'->>'stream_id'),
                ((document->'source_stream'->>'through_sequence')::integer))
            WHERE document ? 'source_stream'
    """)
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION broker_protect_position_ledger() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Broker position ledger evidence is immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in (_entries, _checks):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable ON {table.name}")
        connection.exec_driver_sql(f"""
            CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table.name}
            FOR EACH ROW EXECUTE FUNCTION broker_protect_position_ledger()
        """)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _time(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() != UTC.utcoffset(moment):
        raise ValueError("ledger time must use UTC")
    return moment


def _economic(fill: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in fill.items()
        if key
        not in {
            "contract_id",
            "source_batch_id",
            "source_sequence",
            "source_stream_id",
            "source_received_at",
        }
    }


def _problems(batch: dict[str, Any], day: str) -> list[dict[str, Any]]:
    reasons = []
    if batch["status"] != "COMPLETE":
        reasons.append("QUERY_NOT_COMPLETE")
    if batch["completeness"]["identity"] != "CONFIRMED":
        reasons.append("TD_ACCOUNT_IDENTITY_NOT_CONFIRMED")
    if batch["completeness"]["trading_day"] != day:
        reasons.append("SETTLEMENT_AND_NEW_TRADING_DAY_NOT_SUPPORTED")
    return [{"code": reason, "source_batch_id": batch["batch_id"]} for reason in reasons]


def _stream_reference(prefix: dict[str, Any], after_sequence: int) -> dict[str, Any]:
    return {
        **{
            key: prefix[key]
            for key in (
                "stream_id",
                "through_sequence",
                "prefix_hash",
                "binding_hash",
                "first_received_at",
                "first_committed_at",
                "segment_received_at",
                "segment_committed_at",
                "last_received_at",
                "last_committed_at",
            )
        },
        "after_sequence": after_sequence,
    }


class BrokerLedger:
    """A same-day append-only position book, never a cash or execution authority."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._records = BrokerRecords(engine)
        self._baselines = BrokerBaselines(engine)

    def _raw(self, table: Table, identifier: UUID) -> dict[str, Any]:
        if not isinstance(identifier, UUID):
            raise ValueError("position ledger commands require UUID identities")
        primary = list(table.primary_key)[0]
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(table).where(primary == identifier))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("broker position record not found")
        document = row["document"]
        if (
            not isinstance(document, dict)
            or _hash(document) != row["sha256"]
            or document.get(primary.name) != str(identifier)
            or _time(document["recorded_at"]) != row["recorded_at"]
        ):
            raise ValueError("broker position evidence is damaged")
        keys = (
            ("baseline_id", "source_batch_id", "ordinal")
            if table is _entries
            else ("entry_id", "query_batch_id")
        )
        if any(
            document.get(key) != (str(row[key]) if isinstance(row[key], UUID) else row[key])
            for key in keys
        ):
            raise ValueError("broker position index differs from its fixed evidence")
        return document

    def _history(self, baseline_id: UUID, *, through: int | None = None) -> list[dict[str, Any]]:
        baseline = self._baselines.get_baseline(baseline_id)
        query = select(_entries.c.entry_id).where(_entries.c.baseline_id == baseline_id)
        if through is not None:
            query = query.where(_entries.c.ordinal <= through)
        with self._engine.connect() as connection:
            identities = list(
                connection.scalars(query.order_by(_entries.c.ordinal).limit(_MAX_ENTRIES + 1))
            )
        if len(identities) > _MAX_ENTRIES:
            raise ValueError("position ledger exceeds its bounded daily entry limit")
        history: list[dict[str, Any]] = []
        for ordinal, identifier in enumerate(identities, 1):
            entry = self._raw(_entries, identifier)
            previous = history[-1] if history else None
            source: dict[str, Any] = self._records.get(UUID(entry["source_batch_id"]))
            if (
                entry["ordinal"] != ordinal
                or entry["baseline_hash"] != _hash(baseline)
                or entry["source_hash"] != _hash(source)
                or entry["previous_entry_id"]
                != (None if previous is None else previous["entry_id"])
                or entry["previous_hash"] != (None if previous is None else _hash(previous))
            ):
                raise ValueError("position ledger chain or source evidence is damaged")
            prefix = self._entry_stream(entry)
            for fill in entry["added_fills"]:
                if fill["contract_id"] is not None:
                    instrument = (
                        source["completeness"]["sections"]["instrument"]["rows"][0]
                        if prefix is None
                        else prefix["binding"]["terms"]
                    )
                    contract = verify_broker_contract(
                        self._engine, UUID(fill["contract_id"]), instrument
                    )
                    if (contract.exchange, contract.symbol) != (fill["exchange"], fill["symbol"]):
                        raise ValueError("position fill differs from its canonical contract")
            history.append(entry)
        return history

    def _entry_stream(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """Verify exactly the source segment fixed by this entry, not the stream's tail."""
        source = entry.get("source_stream")
        if source is None:
            return None
        from northstar_quant.broker.stream_records import read_stream_account_prefix

        prefix: dict[str, Any] = read_stream_account_prefix(
            self._engine,
            UUID(source["stream_id"]),
            source["through_sequence"],
            after_sequence=source["after_sequence"],
        )
        if (
            _stream_reference(prefix, source["after_sequence"]) != source
            or prefix["binding"]["request"]["query_batch_id"] != entry["source_batch_id"]
        ):
            raise ValueError("position entry stream source differs from its fixed evidence")
        return {**prefix, "after_sequence": source["after_sequence"]}

    def get(self, entry_id: UUID) -> dict[str, Any]:
        entry = self._raw(_entries, entry_id)
        history = self._history(UUID(entry["baseline_id"]), through=entry["ordinal"])
        if not history or history[-1] != entry:
            raise ValueError("position ledger entry is outside its fixed chain")
        return entry

    def get_fill(self, entry_id: UUID, fill_id: str) -> dict[str, Any]:
        """Read one retained execution through an exact verified account prefix."""
        entry = self._raw(_entries, entry_id)
        history = self._history(UUID(entry["baseline_id"]), through=entry["ordinal"])
        if not history or history[-1] != entry:
            raise ValueError("position ledger entry is outside its fixed chain")
        matches = [
            fill for item in history for fill in item["added_fills"] if fill["fill_id"] == fill_id
        ]
        if len(matches) != 1:
            raise ValueError("account prefix lacks one unique retained execution")
        return dict(matches[0])

    def get_check(self, check_id: UUID) -> dict[str, Any]:
        check = self._raw(_checks, check_id)
        entry = self.get(UUID(check["entry_id"]))
        source = self._records.get(UUID(check["query_batch_id"]))
        if check["entry_hash"] != _hash(entry) or check["query_hash"] != _hash(source):
            raise ValueError("position comparison input evidence is damaged")
        return check

    @staticmethod
    def _after(batch: dict[str, Any], recorded_at: str) -> None:
        capture = batch["capture"]
        if capture is None:
            raise ValueError("wait for the independent query to finish")
        if _time(batch["created_at"]) <= _time(recorded_at) or _time(
            capture["started_at"]
        ) <= _time(recorded_at):
            raise ValueError("requires an independent query begun after the fixed ledger input")
        if _time(capture["finished_at"]) >= datetime.now(UTC):
            raise ValueError("query must already have finished")

    def ingest(
        self, baseline_id: UUID, source_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, Any]:
        if not all(isinstance(value, UUID) for value in (baseline_id, source_batch_id, request_id)):
            raise ValueError("position ledger commands require UUID identities")
        return self._ingest(baseline_id, source_batch_id, request_id=request_id)

    def ingest_stream(
        self, baseline_id: UUID, stream_id: UUID, through_sequence: int, *, request_id: UUID
    ) -> dict[str, Any]:
        """Apply a retained callback prefix, including while shadow calculation is paused.

        Later prefixes advance contiguously, sharing the existing account lock and
        fill identity set with query ingestion. No query, connection, fee estimate,
        fill synthesis or execution permission is created by this operation.
        """
        if not all(isinstance(value, UUID) for value in (baseline_id, stream_id, request_id)):
            raise ValueError("stream ledger commands require UUID identities")
        if type(through_sequence) is not int or not 1 <= through_sequence <= 100000:
            raise ValueError("stream ledger requires a bounded retained prefix")
        return self._ingest(
            baseline_id,
            None,
            stream_id=stream_id,
            through_sequence=through_sequence,
            request_id=request_id,
        )

    def bind_stream(self, baseline_id: UUID, stream_id: UUID) -> dict[str, Any]:
        """Fix one existing account baseline for local saved-callback processing."""
        from northstar_quant.accounting.stream_progress import _StreamAccount

        return _StreamAccount(self).bind(baseline_id, stream_id)

    def advance_stream(
        self, stream_id: UUID, through_sequence: int, *, transaction: Connection | None = None
    ) -> dict[str, Any]:
        """Apply saved asynchronous account observations, never strategy or network work."""
        from northstar_quant.accounting.stream_progress import _StreamAccount

        return _StreamAccount(self).advance(stream_id, through_sequence, transaction=transaction)

    def stream_progress(self, stream_id: UUID) -> dict[str, Any]:
        """Read bounded local progress; this is not proof of external account coverage."""
        from northstar_quant.accounting.stream_progress import _StreamAccount

        return _StreamAccount(self).progress(stream_id)

    def _ingest(
        self,
        baseline_id: UUID,
        source_batch_id: UUID | None,
        *,
        request_id: UUID,
        stream_id: UUID | None = None,
        through_sequence: int | None = None,
        account_after_sequence: int | None = None,
        transaction: Connection | None = None,
    ) -> dict[str, Any]:
        # Serialize both the deduplication set and its resulting projection.
        lock_key = int.from_bytes(
            hashlib.sha256(baseline_id.bytes + b"positions").digest()[:8], "big", signed=True
        )
        with (
            nullcontext(transaction) if transaction is not None else write_transaction(self._engine)
        ) as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            try:
                saved = self.get(request_id)
            except LookupError:
                pass
            else:
                saved_stream = saved.get("source_stream")
                if saved["baseline_id"] != str(baseline_id) or (
                    (saved_stream is not None or saved["source_batch_id"] != str(source_batch_id))
                    if stream_id is None
                    else saved_stream is None
                    or saved_stream["stream_id"] != str(stream_id)
                    or saved_stream["through_sequence"] != through_sequence
                ):
                    raise ValueError("position command is already bound to different inputs")
                return saved
            baseline = self._baselines.get_baseline(baseline_id)
            if stream_id is None:
                from northstar_quant.accounting.stream_progress import _StreamAccount

                _StreamAccount(self).reject_pending_query(connection, baseline_id)
            history = self._history(baseline_id)
            if len(history) >= _MAX_ENTRIES:
                raise ValueError("position ledger reached its bounded daily entry limit")
            previous = history[-1] if history else None
            prefix: dict[str, Any] | None = None
            after_sequence = 0
            if stream_id is not None:
                from northstar_quant.broker.stream_records import read_stream_account_prefix

                after_sequence = max(
                    (
                        item["source_stream"]["through_sequence"]
                        for item in history
                        if item.get("source_stream", {}).get("stream_id") == str(stream_id)
                    ),
                    default=0,
                )
                if account_after_sequence is not None:
                    after_sequence = max(after_sequence, account_after_sequence)
                if through_sequence is None or through_sequence <= after_sequence:
                    raise ValueError("stream prefix already has a fixed entry or precedes it")
                prefix = cast(
                    dict[str, Any],
                    read_stream_account_prefix(
                        self._engine,
                        stream_id,
                        through_sequence,
                        after_sequence=after_sequence,
                    ),
                )
                source_batch_id = UUID(prefix["binding"]["request"]["query_batch_id"])
            assert source_batch_id is not None
            batch: dict[str, Any] = self._records.get(source_batch_id)
            if (
                batch["profile"] != baseline["profile"]
                or batch["account_id"] != baseline["account_id"]
            ):
                raise ValueError("position ledger requires the same environment and account")
            if prefix is None:
                self._after(batch, baseline["recorded_at"])
                if any(
                    "source_stream" not in item and item["source_batch_id"] == str(source_batch_id)
                    for item in history
                ):
                    raise ValueError("query already has a fixed ledger entry; read it instead")
                source_start = _time(batch["created_at"])
            else:
                source_start = min(
                    _time(prefix["segment_received_at"]),
                    _time(prefix["segment_committed_at"]),
                )
                if source_start <= _time(baseline["recorded_at"]):
                    raise ValueError("stream segment must begin after fixing the flat baseline")
                if _time(prefix["last_committed_at"]) >= _time(_now()):
                    raise ValueError("stream prefix must already have been committed")
            if previous is not None:
                prior_stream = previous.get("source_stream")
                if prior_stream is None:
                    prior: dict[str, Any] = self._records.get(UUID(previous["source_batch_id"]))
                    source_end = _time(prior["capture"]["finished_at"])
                else:
                    source_end = max(
                        _time(prior_stream["last_received_at"]),
                        _time(prior_stream["last_committed_at"]),
                    )
                ordered_stream_segment = (
                    prefix is not None
                    and prior_stream is not None
                    and _time(prefix["segment_received_at"])
                    >= _time(prior_stream["last_received_at"])
                    and _time(prefix["segment_committed_at"])
                    > _time(prior_stream["last_committed_at"])
                )
                if not ordered_stream_segment and source_start <= source_end:
                    raise ValueError("ledger ingestion requires ordered non-overlapping sources")
            known = {fill["fill_id"]: fill for item in history for fill in item["added_fills"]}
            previous_fill_ids = set(known)
            queried_fill_ids = set()
            queried_sequences = (
                set()
                if prefix is not None
                else {
                    event["sequence"]
                    for event in batch["capture"]["events"]
                    if event["callback"] == "OnRspQryTrade"
                }
            )
            problems = [problem for item in history for problem in item["new_problems"]]
            if prefix is None:
                observations = query_trades(batch)
                new_problems = _problems(batch, baseline["trading_day"])
            else:
                observations, new_problems = stream_trades(
                    prefix, after_sequence, baseline["trading_day"]
                )
            added, duplicate_count = [], 0
            stream_receipts = (
                {}
                if prefix is None
                else {
                    item["event"]["sequence"]: item["event"]["received_at"]
                    for item in prefix["events"]
                }
            )
            contract = None
            for sequence, row in observations:
                locator = {"source_batch_id": str(source_batch_id), "sequence": sequence}
                if prefix is not None:
                    locator["source_stream_id"] = str(stream_id)
                try:
                    fill = decode_trade(row, batch)
                except ValueError:
                    new_problems.append({"code": "TRADE_FIELDS_NOT_CONFIRMED", **locator})
                    continue
                if sequence in queried_sequences:
                    queried_fill_ids.add(fill["fill_id"])
                earlier = known.get(fill["fill_id"])
                if earlier is not None:
                    if _economic(earlier) != fill:
                        new_problems.append(
                            {
                                "code": "TRADE_IDENTITY_CONFLICT",
                                "fill_id": fill["fill_id"],
                                **locator,
                            }
                        )
                    else:
                        duplicate_count += 1
                    continue
                contract_id = None
                try:
                    if fill["exchange"] != "SHFE" or fill["symbol"] != batch["instrument"].upper():
                        raise ValueError("trade has no confirmed supported contract")
                    if contract is None:
                        if prefix is not None:
                            contract = verify_broker_contract(
                                self._engine,
                                UUID(prefix["binding"]["contract_id"]),
                                prefix["binding"]["terms"],
                            )
                        else:
                            instruments = batch["completeness"]["sections"]["instrument"]
                            if instruments["status"] != "COMPLETE" or len(instruments["rows"]) != 1:
                                raise ValueError("trade has no confirmed supported contract")
                            contract = resolve_broker_contract(connection, instruments["rows"][0])
                    contract_id = str(contract.contract_id)
                except ValueError:
                    new_problems.append({"code": "CANONICAL_CONTRACT_NOT_CONFIRMED", **locator})
                if (
                    fill["trading_day"] != baseline["trading_day"]
                    or fill["hedge_flag"] != "1"
                    or fill["offset"] == "UNSUPPORTED"
                ):
                    new_problems.append({"code": "UNSUPPORTED_POSITION_EFFECT", **locator})
                fill.update(
                    contract_id=contract_id,
                    source_batch_id=str(source_batch_id),
                    source_sequence=sequence,
                )
                if prefix is not None:
                    fill.update(
                        source_stream_id=str(stream_id),
                        source_received_at=stream_receipts[sequence],
                    )
                known[fill["fill_id"]] = fill
                added.append(fill)
            if prefix is None and previous_fill_ids - queried_fill_ids:
                new_problems.append(
                    {
                        "code": "RECORDED_TRADES_MISSING_FROM_LATER_QUERY",
                        "source_batch_id": str(source_batch_id),
                    }
                )
            if len(known) > _MAX_FILLS:
                raise ValueError("position ledger exceeds its bounded daily fill limit")
            problems.extend(new_problems)
            positions: list[dict[str, Any]] = []
            if not problems:
                try:
                    projection = project_intraday_positions(
                        date.fromisoformat(baseline["trading_day"]),
                        tuple(
                            PositionChange(
                                UUID(fill["contract_id"]),
                                date.fromisoformat(fill["trading_day"]),
                                fill["direction"],
                                fill["offset"],
                                fill["quantity_lots"],
                                _time(fill["filled_at"]),
                            )
                            for fill in known.values()
                        ),
                    )
                    for projected_contract_id, amounts in projection.items():
                        fact = next(
                            fill
                            for fill in known.values()
                            if fill["contract_id"] == str(projected_contract_id)
                        )
                        for direction in ("LONG", "SHORT"):
                            positions.append(
                                {
                                    "contract_id": str(projected_contract_id),
                                    "exchange": fact["exchange"],
                                    "symbol": fact["symbol"],
                                    "hedge_flag": "1",
                                    "direction": direction,
                                    "today_lots": amounts[f"{direction.lower()}_today"],
                                    "yesterday_lots": amounts[f"{direction.lower()}_yesterday"],
                                }
                            )
                except ValueError:
                    problem = {
                        "code": "POSITION_EFFECTS_CANNOT_BE_RESOLVED",
                        "source_batch_id": str(source_batch_id),
                    }
                    new_problems.append(problem)
                    problems.append(problem)
            now = _now()
            document = {
                "entry_id": str(request_id),
                "baseline_id": str(baseline_id),
                "source_batch_id": str(source_batch_id),
                "baseline_hash": _hash(baseline),
                "source_hash": _hash(batch),
                "ordinal": len(history) + 1,
                "previous_entry_id": None if previous is None else previous["entry_id"],
                "previous_hash": None if previous is None else _hash(previous),
                "recorded_at": now,
                "code_revision": code_revision(),
                "added_fills": added,
                "fill_count": len(known),
                "new_fill_count": len(added),
                "duplicate_count": duplicate_count,
                "new_problems": new_problems,
                "problems": problems,
                "status": "UNKNOWN" if problems else "READY",
                "position_projection": {
                    "status": "UNKNOWN" if problems else "KNOWN",
                    "positions": positions,
                },
                "scope": "SAME_DAY_FLAT_START_SHFE_SPECULATION",
                "cash_projection": None,
                "fees": "NOT_ESTABLISHED",
                "reconciliation": "UNRECONCILED",
                "execution": dict(_EXECUTION),
            }
            if prefix is not None:
                document["source_stream"] = _stream_reference(prefix, after_sequence)
            connection.execute(
                _entries.insert().values(
                    entry_id=request_id,
                    baseline_id=baseline_id,
                    source_batch_id=source_batch_id,
                    ordinal=document["ordinal"],
                    recorded_at=_time(now),
                    document=document,
                    sha256=_hash(document),
                )
            )
        return document if transaction is not None else self.get(request_id)

    def compare(self, entry_id: UUID, query_batch_id: UUID, *, request_id: UUID) -> dict[str, Any]:
        if not all(isinstance(value, UUID) for value in (entry_id, query_batch_id, request_id)):
            raise ValueError("position ledger commands require UUID identities")
        try:
            saved = self.get_check(request_id)
        except LookupError:
            pass
        else:
            if (saved["entry_id"], saved["query_batch_id"]) != (str(entry_id), str(query_batch_id)):
                raise ValueError("position comparison is already bound to different inputs")
            return saved
        entry = self.get(entry_id)
        baseline = self._baselines.get_baseline(UUID(entry["baseline_id"]))
        batch: dict[str, Any] = self._records.get(query_batch_id)
        if batch["profile"] != baseline["profile"] or batch["account_id"] != baseline["account_id"]:
            raise ValueError("position comparison requires the same environment and account")
        self._after(batch, entry["recorded_at"])
        history = self._history(UUID(entry["baseline_id"]), through=entry["ordinal"])
        known = {fill["fill_id"]: fill for item in history for fill in item["added_fills"]}
        problems = list(entry["problems"]) + _problems(batch, baseline["trading_day"])
        unrecorded = []
        observed: dict[str, dict[str, Any]] = {}
        for sequence, row in query_trades(batch):
            try:
                fill = decode_trade(row, batch)
            except ValueError:
                problems.append({"code": "TRADE_FIELDS_NOT_CONFIRMED", "sequence": sequence})
                continue
            if fill["fill_id"] in observed and observed[fill["fill_id"]] != fill:
                problems.append({"code": "TRADE_IDENTITY_CONFLICT", "fill_id": fill["fill_id"]})
            observed[fill["fill_id"]] = fill
            if fill["fill_id"] not in known:
                if fill not in unrecorded:
                    unrecorded.append(fill)
            elif _economic(known[fill["fill_id"]]) != fill:
                problems.append({"code": "TRADE_IDENTITY_CONFLICT", "fill_id": fill["fill_id"]})
        if set(known) - set(observed):
            problems.append({"code": "RECORDED_TRADES_MISSING_FROM_LATER_QUERY"})
        if any(
            event["callback"] in {"OnRtnTrade", "OnRtnOrder"}
            for event in batch["capture"]["events"]
        ):
            problems.append({"code": "ACCOUNT_ACTIVITY_DURING_QUERY"})
        positions, position_problems = _compare_positions(entry, batch)
        problems.extend(position_problems)
        orders = batch["completeness"]["sections"]["orders"]
        if orders["status"] != "COMPLETE":
            problems.append({"code": "ORDERS_NOT_COMPLETE"})
        changed = unrecorded or any(
            row["delta_today"] or row["delta_yesterday"] for row in positions
        )
        now = _now()
        document = {
            "check_id": str(request_id),
            "entry_id": str(entry_id),
            "baseline_id": entry["baseline_id"],
            "query_batch_id": str(query_batch_id),
            "entry_hash": _hash(entry),
            "query_hash": _hash(batch),
            "recorded_at": now,
            "code_revision": code_revision(),
            "status": "UNKNOWN" if problems else "DIFFERENCES" if changed else "MATCHED",
            "positions": positions,
            "problems": problems,
            "unrecorded_fills": unrecorded,
            "observed_orders": orders["rows"] if orders["status"] == "COMPLETE" else None,
            "observed_positions": batch["completeness"]["sections"]["positions"]["rows"],
            "scope": "POSITION_QUANTITIES_ONLY",
            "cash_projection": None,
            "reconciliation": "UNRECONCILED",
            "execution": dict(_EXECUTION),
            "limitations": [
                "NO_CONFIRMED_FEES_CASHFLOW_OR_SETTLEMENT_LEDGER",
                "NO_ORDER_LIFECYCLE_RECONCILIATION",
                "QUERIES_ARE_NOT_ATOMIC",
                "NO_CONTINUOUS_EVENT_COVERAGE_OR_CURRENT_SAFETY_CLAIM",
            ],
        }
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        with write_transaction(self._engine) as connection:
            connection.execute(
                (sqlite_insert if connection.dialect.name == "sqlite" else pg_insert)(_checks)
                .values(
                    check_id=request_id,
                    entry_id=entry_id,
                    query_batch_id=query_batch_id,
                    recorded_at=_time(now),
                    document=document,
                    sha256=_hash(document),
                )
                .on_conflict_do_nothing()
            )
        try:
            saved = self.get_check(request_id)
        except LookupError as error:
            raise ValueError(
                "query already has a fixed position comparison; read it instead"
            ) from error
        if (saved["entry_id"], saved["query_batch_id"]) != (str(entry_id), str(query_batch_id)):
            raise ValueError("position comparison is already bound to different inputs")
        return saved

    def order_review_inputs(
        self, position_check_id: UUID
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Return verified, fixed account facts; no ORM tables escape this operation."""
        parent = self.get_check(position_check_id)
        entry = self.get(UUID(parent["entry_id"]))
        history = self._history(UUID(entry["baseline_id"]), through=entry["ordinal"])
        sources = [
            self._entry_stream(item) or self._records.get(UUID(item["source_batch_id"]))
            for item in history
        ]
        sources.append(self._records.get(UUID(parent["query_batch_id"])))
        known = [fill for item in history for fill in item["added_fills"]]
        return parent, sources, known

    def comparison_ids(self, baseline_id: UUID) -> list[UUID]:
        """Identify account comparisons for an owner-composed review history."""
        self._baselines.get_baseline(baseline_id)
        with self._engine.connect() as connection:
            return list(
                connection.scalars(
                    select(_checks.c.check_id)
                    .join(_entries, _entries.c.entry_id == _checks.c.entry_id)
                    .where(_entries.c.baseline_id == baseline_id)
                )
            )

    def context(self, query_batch_id: UUID) -> dict[str, Any]:
        baseline = self._baselines.context(query_batch_id)["baseline"]
        if baseline is None:
            return {
                "baseline_id": None,
                "current": None,
                "source_entry": None,
                "current_check": None,
                "entries": [],
                "checks": [],
            }
        history = self._history(UUID(baseline["baseline_id"]))
        current = history[-1] if history else None
        with self._engine.connect() as connection:
            ids = list(
                connection.scalars(
                    select(_checks.c.check_id)
                    .join(_entries, _entries.c.entry_id == _checks.c.entry_id)
                    .where(_entries.c.baseline_id == UUID(baseline["baseline_id"]))
                    .order_by(_checks.c.recorded_at.desc())
                    .limit(20)
                )
            )
            current_id = (
                None
                if current is None
                else connection.scalar(
                    select(_checks.c.check_id).where(
                        _checks.c.entry_id == UUID(current["entry_id"]),
                        _checks.c.query_batch_id == query_batch_id,
                    )
                )
            )
        return {
            "baseline_id": baseline["baseline_id"],
            "current": current,
            "source_entry": next(
                (
                    item
                    for item in history
                    if "source_stream" not in item
                    and item["source_batch_id"] == str(query_batch_id)
                ),
                None,
            ),
            "current_check": None if current_id is None else self.get_check(current_id),
            "entries": list(reversed(history[-20:])),
            "checks": [self.get_check(identifier) for identifier in ids],
        }

    def verify_all(self) -> dict[str, int]:
        counts = {"position_entries_count": 0, "position_checks_count": 0}
        with self._engine.connect() as connection:
            baseline_ids = list(connection.scalars(select(_entries.c.baseline_id).distinct()))
            for baseline_id in baseline_ids:
                counts["position_entries_count"] += len(self._history(baseline_id))
            for check_id in connection.scalars(select(_checks.c.check_id)).yield_per(100):
                self.get_check(check_id)
                counts["position_checks_count"] += 1
        from northstar_quant.accounting.stream_progress import _StreamAccount

        _StreamAccount(self).verify_all()
        return counts


def _compare_positions(
    entry: dict[str, Any], batch: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected = {
        (item["exchange"], item["symbol"], item["hedge_flag"], item["direction"]): item
        for item in entry["position_projection"]["positions"]
    }
    observed, complete, problems = position_observations(batch)
    result = []
    for key in sorted(set(expected) | set(observed)):
        item = expected.get(key)
        values = observed.get(key, {"today": 0, "yesterday": 0})
        expected_today: int | None = 0 if item is None else item["today_lots"]
        expected_yesterday: int | None = 0 if item is None else item["yesterday_lots"]
        if entry["position_projection"]["status"] != "KNOWN":
            expected_today = expected_yesterday = None
        observed_today, observed_yesterday = (
            (values["today"], values["yesterday"]) if complete else (None, None)
        )
        result.append(
            {
                "contract_id": None if item is None else item["contract_id"],
                "exchange": key[0],
                "symbol": key[1],
                "hedge_flag": key[2],
                "direction": key[3],
                "expected_today": expected_today,
                "expected_yesterday": expected_yesterday,
                "observed_today": observed_today,
                "observed_yesterday": observed_yesterday,
                "delta_today": None
                if observed_today is None or expected_today is None
                else observed_today - expected_today,
                "delta_yesterday": None
                if observed_yesterday is None or expected_yesterday is None
                else observed_yesterday - expected_yesterday,
            }
        )
    return result, problems
