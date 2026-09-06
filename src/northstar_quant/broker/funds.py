"""Append account-level money observations without manufacturing fill expenses.

Each entry fixes a real query, its predecessor and the position book visible at
recording. Reported balances are not rebuilt by adding cumulative fees or P&L.
The observation interval cannot establish which individual trades it includes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    Column,
    Connection,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from northstar_quant.accounting import ACCOUNT_AMOUNT_FIELDS, compare_account_amounts
from northstar_quant.broker.baselines import BrokerBaselines
from northstar_quant.broker.ledger import BrokerLedger
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.runtime import implementation_hash

_metadata = MetaData()
_entries = Table(
    "broker_funds_entries",
    _metadata,
    Column("entry_id", PGUUID(as_uuid=True), primary_key=True),
    Column("baseline_id", PGUUID(as_uuid=True), nullable=False),
    Column("source_batch_id", PGUUID(as_uuid=True), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("document", JSONB, nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("baseline_id", "ordinal"),
    UniqueConstraint("baseline_id", "source_batch_id"),
)


def initialize_broker_funds(connection: Connection) -> None:
    _metadata.create_all(connection)
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


def _observation(batch: dict[str, Any]) -> dict[str, Any]:
    section = batch["completeness"]["sections"]["account"]
    capture = batch["capture"]
    problems = []
    if batch["status"] != "COMPLETE":
        problems.append("QUERY_NOT_COMPLETE")
    if batch["completeness"]["identity"] != "CONFIRMED":
        problems.append("TD_ACCOUNT_IDENTITY_NOT_CONFIRMED")
    rows = section["rows"]
    row = rows[0] if section["status"] == "COMPLETE" and rows and len(rows) == 1 else None
    if row is None:
        problems.append("ONE_COMPLETE_CNY_ACCOUNT_REQUIRED")
    elif (
        row.get("BrokerID") != batch["profile"]["broker_id"]
        or row.get("AccountID") != batch["account_id"]
        or row.get("CurrencyID") != "CNY"
        or row.get("BizType") != "1"
        or row.get("TradingDay") != batch["completeness"]["trading_day"]
        or type(row.get("SettlementID")) is not int
    ):
        problems.append("ACCOUNT_SCOPE_NOT_CONFIRMED")
    callbacks = (
        []
        if capture is None
        else [
            {"sequence": event["sequence"], "received_at": event["received_at"]}
            for event in capture["events"]
            if event["channel"] == "TD"
            and event["callback"] == "OnRspQryTradingAccount"
            and event["request_id"] == section["request_id"]
            and event["data"] is not None
            and event["error_id"] == 0
        ]
    )
    if len(callbacks) != 1:
        problems.append("ACCOUNT_RECEIPT_NOT_UNIQUE")
    scope_confirmed = not problems
    amounts = (
        {} if row is None else {name: row[name] for name in ACCOUNT_AMOUNT_FIELDS if name in row}
    )
    # The arithmetic owner checks finite exact values, including negative actual balances.
    checked = compare_account_amounts(amounts, amounts, same_scope=True)
    problems.extend(
        code for code in cast(list[str], checked["problems"]) if not code.endswith("_PREVIOUS")
    )
    return {
        "source_batch_id": batch["batch_id"],
        "query_started_at": None if capture is None else capture["started_at"],
        "query_finished_at": None if capture is None else capture["finished_at"],
        "account_receipts": callbacks,
        "scope": None
        if row is None
        else {
            name: row.get(name)
            for name in (
                "BrokerID",
                "AccountID",
                "CurrencyID",
                "BizType",
                "TradingDay",
                "SettlementID",
            )
        },
        "amounts": amounts,
        "scope_confirmed": scope_confirmed,
        "problems": sorted(set(problems)),
        "account_activity_during_query": capture is not None
        and any(event["callback"] in {"OnRtnTrade", "OnRtnOrder"} for event in capture["events"]),
    }


class BrokerFunds:
    """One bounded account book of cumulative observations and signed intervals."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("broker money book requires PostgreSQL")
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
        result: list[dict[str, Any]] = []
        for ordinal, row in enumerate(rows, 1):
            entry = row["document"]
            previous = result[-1] if result else None
            source = self._records.get(row["source_batch_id"])
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
            position = entry["position_reference"]
            if (
                position is not None
                and _hash(self._positions.get(UUID(position["entry_id"]))) != position["sha256"]
            ):
                raise ValueError("account money position reference is damaged")
            result.append(entry)
        return result

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
        with self._engine.begin() as connection:
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
            initial = _observation(self._records.get(UUID(baseline["source_batch_id"])))
            prior = initial if previous is None else previous["observation"]
            if _time(capture["started_at"]) <= _time(prior["query_finished_at"]):
                raise ValueError("money observations require ordered non-overlapping queries")
            observation = _observation(batch)
            same_scope = (
                prior["scope_confirmed"]
                and observation["scope_confirmed"]
                and prior["scope"] == observation["scope"]
            )
            interval = compare_account_amounts(
                prior["amounts"], observation["amounts"], same_scope=bool(same_scope)
            )
            since_baseline = compare_account_amounts(
                initial["amounts"],
                observation["amounts"],
                same_scope=(
                    initial["scope_confirmed"]
                    and observation["scope_confirmed"]
                    and initial["scope"] == observation["scope"]
                ),
            )
            current = self._positions.context(source_batch_id)["current"]
            problems = sorted(
                set(observation["problems"] + interval["problems"] + since_baseline["problems"])
            )
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
                "implementation_hash": implementation_hash(),
                "observation": observation,
                "interval_start": {
                    "source_batch_id": prior["source_batch_id"],
                    "account_receipts": prior["account_receipts"],
                },
                "interval": interval,
                "since_baseline": since_baseline,
                "position_reference": None
                if current is None
                else {
                    "entry_id": current["entry_id"],
                    "ordinal": current["ordinal"],
                    "sha256": _hash(current),
                },
                "status": "UNKNOWN" if problems else "OBSERVED",
                "problems": problems,
                "reconciliation": "UNRECONCILED",
                "execution": {"order_sending": False, "cancel_sending": False},
                "limitations": [
                    "CUMULATIVE_ACCOUNT_AMOUNTS_NOT_INDIVIDUAL_FILL_FEES",
                    "QUERY_RECEIPT_TIME_NOT_ACCOUNT_SNAPSHOT_TIME",
                    "POSITION_REFERENCE_DOES_NOT_ESTABLISH_FILL_COVERAGE",
                    "NO_ATOMIC_ACCOUNT_CUT_OR_CONTINUOUS_FUNDS_COVERAGE",
                    "NO_RECONSTRUCTED_BALANCE_OR_AVAILABLE_FORMULA",
                    "NO_SETTLEMENT_RESERVATION_OR_EXECUTION_AUTHORITY",
                ],
            }
            connection.execute(
                insert(_entries)
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
