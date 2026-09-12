"""Append account-level money observations without manufacturing fill expenses.

Each entry fixes a real query, its predecessor and the position book visible at
recording. Reported balances are not rebuilt by adding cumulative fees or P&L.
The observation interval cannot establish which individual trades it includes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from northstar_quant import code_revision
from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.accounting.observations import compare_account_amounts
from northstar_quant.broker.account_reports import account_observation
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.persistence.sql import UTCDateTime, write_transaction

_metadata = MetaData()
_entries = Table(
    "broker_funds_entries",
    _metadata,
    Column("entry_id", Uuid(as_uuid=True), primary_key=True),
    Column("baseline_id", Uuid(as_uuid=True), nullable=False),
    Column("source_batch_id", Uuid(as_uuid=True), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("recorded_at", UTCDateTime(), nullable=False),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("baseline_id", "ordinal"),
    UniqueConstraint("baseline_id", "source_batch_id"),
)


def initialize_broker_funds(connection: Connection) -> None:
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        for table in ("broker_funds_entries",):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                    f"BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Confirmed facts are immutable'); END"
                )
        return
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION broker_protect_funds() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Broker money observations are immutable';
        END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON broker_funds_entries;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON broker_funds_entries
            FOR EACH ROW EXECUTE FUNCTION broker_protect_funds();
    """)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() != UTC.utcoffset(result):
        raise ValueError("account observation time must use UTC")
    return result


def _comparison(
    initial: dict[str, Any], prior: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    """Rebuild derived amounts and uncertainty from retained broker observations."""

    def compare(previous: dict[str, Any]) -> dict[str, object]:
        return compare_account_amounts(
            previous["amounts"],
            observation["amounts"],
            same_scope=bool(
                previous["scope_confirmed"]
                and observation["scope_confirmed"]
                and previous["scope"] == observation["scope"]
            ),
        )

    interval, since_baseline = compare(prior), compare(initial)
    problems = sorted(
        set(observation["problems"] + interval["problems"] + since_baseline["problems"])
    )
    return {
        "observation": observation,
        "interval_start": {
            "source_batch_id": prior["source_batch_id"],
            "account_receipts": prior["account_receipts"],
        },
        "interval": interval,
        "since_baseline": since_baseline,
        "status": "UNKNOWN" if problems else "OBSERVED",
        "problems": problems,
        "reconciliation": "UNRECONCILED",
        "execution": {"order_sending": False, "cancel_sending": False},
    }


class BrokerFunds:
    """One bounded account book of cumulative observations and signed intervals."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._records = BrokerRecords(engine)
        self._baselines = BrokerBaselines(engine)
        self._positions = BrokerLedger(engine)

    def _history(self, baseline_id: UUID, *, through: int | None = None) -> list[dict[str, Any]]:
        baseline = self._baselines.get_baseline(baseline_id)
        query = select(_entries).where(_entries.c.baseline_id == baseline_id)
        if through is not None:
            query = query.where(_entries.c.ordinal <= through)
        with self._engine.connect() as connection:
            rows = list(
                connection.execute(query.order_by(_entries.c.ordinal).limit(1001)).mappings()
            )
        if len(rows) > 1000:
            raise ValueError("account money book exceeds its bounded entry limit")
        initial = account_observation(self._records.get(UUID(baseline["source_batch_id"])))
        result: list[dict[str, Any]] = []
        for ordinal, row in enumerate(rows, 1):
            entry = row["document"]
            previous = result[-1] if result else None
            source: dict[str, Any] = self._records.get(row["source_batch_id"])
            if (
                _hash(entry) != row["sha256"]
                or entry["entry_id"] != str(row["entry_id"])
                or entry["baseline_id"] != str(baseline_id)
                or entry["source_batch_id"] != str(row["source_batch_id"])
                or entry["ordinal"] != ordinal
                or row["ordinal"] != ordinal
                or _time(entry["recorded_at"]) != row["recorded_at"]
                or entry["baseline_hash"] != _hash(baseline)
                or entry["source_hash"] != _hash(source)
                or entry["previous_entry_id"]
                != (None if previous is None else previous["entry_id"])
                or entry["previous_hash"] != (None if previous is None else _hash(previous))
            ):
                raise ValueError("account money evidence or fixed source chain is damaged")
            prior = initial if previous is None else previous["observation"]
            observation = account_observation(source)
            capture = source["capture"]
            if (
                source["profile"] != baseline["profile"]
                or source["account_id"] != baseline["account_id"]
                or capture is None
                or _time(capture["started_at"]) <= _time(baseline["recorded_at"])
                or _time(capture["started_at"]) <= _time(prior["query_finished_at"])
                or _time(capture["finished_at"]) >= row["recorded_at"]
                or any(
                    entry.get(key) != value
                    for key, value in _comparison(initial, prior, observation).items()
                )
            ):
                raise ValueError("account money projection differs from retained broker evidence")
            self._verify_position(entry["position_reference"], baseline_id, row["recorded_at"])
            result.append(entry)
        return result

    def _verify_position(
        self, reference: dict[str, Any] | None, baseline_id: UUID, recorded_at: datetime
    ) -> None:
        if reference is None:
            return
        retained = self._positions.get(UUID(reference["entry_id"]))
        if (
            _hash(retained) != reference["sha256"]
            or retained["baseline_id"] != str(baseline_id)
            or retained["ordinal"] != reference["ordinal"]
            or _time(retained["recorded_at"]) > recorded_at
        ):
            raise ValueError("account money position reference is damaged")

    def get(self, entry_id: UUID) -> dict[str, Any]:
        if not isinstance(entry_id, UUID):
            raise ValueError("money book requires UUID identities")
        with self._engine.connect() as connection:
            row = connection.execute(
                select(_entries.c.baseline_id, _entries.c.ordinal).where(
                    _entries.c.entry_id == entry_id
                )
            ).one_or_none()
        if row is None:
            raise LookupError("account money entry not found")
        history = self._history(row.baseline_id, through=row.ordinal)
        if not history or history[-1]["entry_id"] != str(entry_id):
            raise ValueError("account money entry is outside its fixed chain")
        return history[-1]

    def observe(
        self, baseline_id: UUID, source_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, Any]:
        if not all(isinstance(value, UUID) for value in (baseline_id, source_batch_id, request_id)):
            raise ValueError("money book commands require UUID identities")
        key = int.from_bytes(
            hashlib.sha256(baseline_id.bytes + b"money").digest()[:8], "big", signed=True
        )
        with write_transaction(self._engine) as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            try:
                saved = self.get(request_id)
            except LookupError:
                pass
            else:
                if (saved["baseline_id"], saved["source_batch_id"]) != (
                    str(baseline_id),
                    str(source_batch_id),
                ):
                    raise ValueError("money command already has different fixed inputs")
                return saved
            baseline = self._baselines.get_baseline(baseline_id)
            batch: dict[str, Any] = self._records.get(source_batch_id)
            if (
                batch["profile"] != baseline["profile"]
                or batch["account_id"] != baseline["account_id"]
            ):
                raise ValueError("money observation requires the same environment and account")
            capture = batch["capture"]
            if capture is None or _time(capture["finished_at"]) >= datetime.now(UTC):
                raise ValueError("money observation requires a finished saved query")
            if _time(capture["started_at"]) <= _time(baseline["recorded_at"]):
                raise ValueError("money query must begin after the fixed baseline")
            history = self._history(baseline_id)
            if len(history) >= 1000:
                raise ValueError("money book reached its bounded entry limit")
            if any(item["source_batch_id"] == str(source_batch_id) for item in history):
                raise ValueError("query already has a money entry; read it instead")
            previous = history[-1] if history else None
            initial = account_observation(self._records.get(UUID(baseline["source_batch_id"])))
            prior = initial if previous is None else previous["observation"]
            if _time(capture["started_at"]) <= _time(prior["query_finished_at"]):
                raise ValueError("money observations require ordered non-overlapping queries")
            observation = account_observation(batch)
            comparison = _comparison(initial, prior, observation)
            current = self._positions.context(source_batch_id)["current"]
            now = datetime.now(UTC)
            document = {
                "entry_id": str(request_id),
                "baseline_id": str(baseline_id),
                "source_batch_id": str(source_batch_id),
                "ordinal": len(history) + 1,
                "baseline_hash": _hash(baseline),
                "source_hash": _hash(batch),
                "previous_entry_id": None if previous is None else previous["entry_id"],
                "previous_hash": None if previous is None else _hash(previous),
                "recorded_at": now.isoformat(),
                "code_revision": code_revision(),
                **comparison,
                "position_reference": None
                if current is None
                else {
                    "entry_id": current["entry_id"],
                    "ordinal": current["ordinal"],
                    "sha256": _hash(current),
                },
                "limitations": [
                    "CUMULATIVE_ACCOUNT_AMOUNTS_NOT_INDIVIDUAL_FILL_FEES",
                    "QUERY_RECEIPT_TIME_NOT_ACCOUNT_SNAPSHOT_TIME",
                    "POSITION_REFERENCE_DOES_NOT_ESTABLISH_FILL_COVERAGE",
                    "NO_ATOMIC_ACCOUNT_CUT_OR_CONTINUOUS_FUNDS_COVERAGE",
                    "NO_RECONSTRUCTED_BALANCE_OR_AVAILABLE_FORMULA",
                    "NO_SETTLEMENT_RESERVATION_OR_EXECUTION_AUTHORITY",
                ],
            }
            self._verify_position(document["position_reference"], baseline_id, now)
            connection.execute(
                (sqlite_insert if connection.dialect.name == "sqlite" else pg_insert)(_entries)
                .values(
                    entry_id=request_id,
                    baseline_id=baseline_id,
                    source_batch_id=source_batch_id,
                    ordinal=document["ordinal"],
                    recorded_at=now,
                    document=document,
                    sha256=_hash(document),
                )
                .on_conflict_do_nothing()
            )
        saved = self.get(request_id)
        if (saved["baseline_id"], saved["source_batch_id"]) != (
            str(baseline_id),
            str(source_batch_id),
        ):
            raise ValueError("money command already has different fixed inputs")
        return saved

    def context(self, query_batch_id: UUID) -> dict[str, Any]:
        baseline = self._baselines.context(query_batch_id)["baseline"]
        history = [] if baseline is None else self._history(UUID(baseline["baseline_id"]))
        return {
            "baseline_id": None if baseline is None else baseline["baseline_id"],
            "current": history[-1] if history else None,
            "source_entry": next(
                (entry for entry in history if entry["source_batch_id"] == str(query_batch_id)),
                None,
            ),
            "entries": list(reversed(history[-20:])),
        }

    def verify_all(self) -> int:
        with self._engine.connect() as connection:
            ids = list(connection.scalars(select(_entries.c.baseline_id).distinct()))
        return sum(len(self._history(identifier)) for identifier in ids)
