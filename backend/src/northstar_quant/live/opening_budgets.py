"""A fixed shadow target and saved broker facts produce a one-lot opening budget.

This is not a sender, a reservation, a replayed historical decision or a current
account certificate. Risk owns the arithmetic; this Module owns evidence,
unsupported facts, immutable commands and their private workspace explanation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
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
    Uuid,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from northstar_quant import code_revision
from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.accounting.observations import compare_account_amounts
from northstar_quant.broker.account_reports import account_observation
from northstar_quant.broker.market import ctp_quote_time
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live.opening_inputs import _amount, _at, calculate
from northstar_quant.live.streams import LiveStreams
from northstar_quant.market_data.sessions import SessionSchedule
from northstar_quant.persistence.sql import UTCDateTime, write_transaction

_metadata = MetaData()
_budgets = Table(
    "broker_opening_budgets",
    _metadata,
    Column("budget_id", Uuid(as_uuid=True), primary_key=True),
    Column("stream_id", Uuid(as_uuid=True), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("query_id", Uuid(as_uuid=True), nullable=False),
    Column("entry_id", Uuid(as_uuid=True), nullable=False),
    Column("recorded_at", UTCDateTime(), nullable=False),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("sha256", String(64), nullable=False),
)


def initialize_opening_budgets(connection: Connection) -> None:
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        for table in ("broker_opening_budgets",):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                    f"BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Confirmed facts are immutable'); END"
                )
        return
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION broker_protect_opening_budget() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Broker opening budget evidence is immutable';
        END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON broker_opening_budgets;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON broker_opening_budgets
            FOR EACH ROW EXECUTE FUNCTION broker_protect_opening_budget();
        CREATE INDEX IF NOT EXISTS broker_opening_budgets_stream_time
            ON broker_opening_budgets(stream_id, recorded_at DESC)
    """)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


class BrokerOpeningBudgets:
    """Persist a non-executable budget from fixed references, never operator account state."""

    def __init__(self, engine: Engine, library: DataLibrary) -> None:
        self._engine, self._library = engine, library
        self._streams = LiveStreams(engine, library)
        self._ledger = BrokerLedger(engine)
        self._baselines = BrokerBaselines(engine)

    def get(self, budget_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(_budgets).where(_budgets.c.budget_id == budget_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("opening budget not found")
        document = row["document"]
        if (
            _hash(document) != row["sha256"]
            or document["budget_id"] != str(budget_id)
            or document["stream_id"] != str(row["stream_id"])
            or document["sequence"] != row["sequence"]
            or document["query_id"] != str(row["query_id"])
            or document["entry_id"] != str(row["entry_id"])
            or _at(document["recorded_at"]) != row["recorded_at"]
        ):
            raise ValueError("opening budget evidence is damaged")
        try:
            decision = self._streams.decision(UUID(document["stream_id"]), document["sequence"])
            query = self._streams.account_query(
                UUID(document["stream_id"]), UUID(document["query_id"])
            )
            entry = self._ledger.get(UUID(document["entry_id"]))
            baseline = self._baselines.get_baseline(UUID(entry["baseline_id"]))
        except LookupError as error:
            raise ValueError("opening budget source evidence is missing") from error
        if (
            decision != document["inputs"]["decision"]
            or _hash(entry) != document["inputs"]["entry_hash"]
            or _hash(baseline) != document["inputs"]["baseline_hash"]
            or _hash(query) != document["inputs"]["query_hash"]
        ):
            raise ValueError("opening budget source differs from its fixed evidence")
        return cast(dict[str, Any], document)

    def create(
        self,
        stream_id: UUID,
        sequence: int,
        query_id: UUID,
        entry_id: UUID,
        *,
        limit_price: Decimal,
        request_id: UUID,
    ) -> dict[str, Any]:
        if not all(
            isinstance(value, UUID) for value in (stream_id, query_id, entry_id, request_id)
        ):
            raise ValueError("opening budgets require UUID identities")
        if type(sequence) is not int or not 1 <= sequence <= 100_000:
            raise ValueError("opening budget requires one retained decision sequence")
        if not isinstance(limit_price, Decimal) or not limit_price.is_finite() or limit_price <= 0:
            raise ValueError("opening limit price must be a positive exact decimal")
        price = _amount(str(limit_price))
        request = {
            "stream_id": str(stream_id),
            "sequence": sequence,
            "query_id": str(query_id),
            "entry_id": str(entry_id),
            "limit_price": decimal_text(price),
        }
        try:
            saved = self.get(request_id)
        except LookupError:
            pass
        else:
            if saved["request"] != request:
                raise ValueError("opening budget identity is already bound to different input")
            return saved
        decision: dict[str, Any] = self._streams.decision(stream_id, sequence)
        entry = self._ledger.get(entry_id)
        baseline = self._baselines.get_baseline(UUID(entry["baseline_id"]))
        batch = self._streams.account_query(stream_id, query_id)
        binding = decision["binding"]
        if any(baseline[key] != binding[key] for key in ("profile", "account_id")):
            raise ValueError("opening budget requires the same environment and account")
        if _at(batch["started_at"]) <= _at(entry["recorded_at"]):
            raise ValueError("receiver query must follow the fixed account entry")
        now = datetime.now(UTC)
        blockers = [
            "PRECHECK_ONLY_NO_EXECUTION_AUTHORIZATION",
            "ACCOUNT_EVENT_COVERAGE_NOT_RECONCILED",
            "NO_DURABLE_ORDER_RESERVATION_OR_SENDER",
            "ACTUAL_FEES_AND_CASH_LEDGER_NOT_ESTABLISHED",
        ]
        intent = decision["result"]["intent"]
        if isinstance(intent, dict):
            if now < _at(intent["generated_at"]) or now >= _at(intent["valid_until"]):
                blockers.append("TARGET_NOT_CURRENT")
            receipts = batch["account_observation"]["account_receipts"]
            if (
                len(receipts) != 1
                or not 0 <= (now - _at(receipts[0]["received_at"])).total_seconds() <= 5
                or not 0 <= (now - _at(batch["finished_at"])).total_seconds() <= 5
            ):
                blockers.append("ACCOUNT_QUERY_NOT_CURRENT_AT_RISK")
        try:
            source_time = ctp_quote_time(
                decision["event"]["data"] or {},
                schedule=(
                    SessionSchedule.from_dict(binding["request"]["schedule"])
                    if "schedule" in binding["request"]
                    else None
                ),
            )
        except ValueError:
            source_time = None
        if (
            source_time is None
            or not -1 <= (now - source_time).total_seconds() <= 5
            or not -1 <= (now - _at(decision["event"]["received_at"])).total_seconds() <= 5
        ):
            blockers.append("MARKET_OBSERVATION_NOT_CURRENT")
        budget: dict[str, object] | None = None
        try:
            budget = calculate(self._engine, decision, entry, batch, price, observed_at=now)
            status, reasons = budget["outcome"], budget["reasons"]
        except ValueError as error:
            status, reasons = "UNKNOWN", [str(error)]
        observation = batch["account_observation"]
        baseline_observation = account_observation(
            BrokerRecords(self._engine).get(UUID(baseline["source_batch_id"]))
        )
        comparison = compare_account_amounts(
            baseline_observation["amounts"],
            observation["amounts"],
            same_scope=bool(
                observation["scope_confirmed"]
                and baseline_observation["scope_confirmed"]
                and observation["scope"] == baseline_observation["scope"]
            ),
        )
        deltas = cast(dict[str, str | None], comparison["deltas"])
        account_matched = (
            not baseline_observation["problems"]
            and not observation["problems"]
            and not comparison["problems"]
            and all(value == "0" for value in deltas.values())
        )
        if not account_matched:
            blockers.append("RECEIVER_ACCOUNT_NOT_MATCHED_TO_LEDGER_BASELINE")
        parts = batch["completeness"]["sections"]
        document = {
            "budget_id": str(request_id),
            **request,
            "account_check": {
                "status": "UNCHANGED" if account_matched else "UNKNOWN",
                "scope": "OBSERVATIONS_ONLY_NOT_ACCOUNT_RECONCILIATION",
                "baseline_observation": baseline_observation,
                "observation": observation,
                "comparison": comparison,
            },
            "request": request,
            "recorded_at": now.isoformat(),
            "code_revision": code_revision(),
            "status": status,
            "reasons": reasons,
            "budget": budget,
            "execution_blockers": blockers,
            "execution": {"order_sending": False, "cancel_sending": False},
            "scope": "SAVED_SHADOW_FIRST_OPENING_BUDGET_ONLY",
            "inputs": {
                "decision": decision,
                "market_source_time": None if source_time is None else source_time.isoformat(),
                "entry_hash": _hash(entry),
                "baseline_hash": _hash(baseline),
                "entry_id": entry["entry_id"],
                "query_hash": _hash(batch),
                "query_window": {key: batch[key] for key in ("started_at", "finished_at")},
                "section_times": {
                    name: {key: part[key] for key in ("first_received_at", "last_received_at")}
                    for name, part in parts.items()
                },
                "funds": parts["account"]["rows"],
                "terms": {
                    name: parts[name]["rows"] for name in ("instrument", "margin", "commission")
                },
            },
            "limitations": [
                "NUMERIC_BUDGET_IS_NOT_CURRENT_ACCOUNT_RECONCILIATION_OR_EXECUTION_PERMISSION",
                "ORIGINAL_SHADOW_DECISION_IS_NOT_MODIFIED_OR_REEXECUTED",
                "ONE_LOT_FLAT_START_CNY_SHFE_SPECULATION_ABSOLUTE_ACCOUNT_RATES_ONLY",
                "BUY_LIMIT_AND_SELL_UPPER_LIMIT_BOUND_TRADE_NOTIONAL_AND_FEE_BUDGET",
                "MARGIN_REFERENCE_MAXIMUM_OF_UPPER_LIMIT_AND_PRE_SETTLEMENT_IS_A_CONSERVATIVE_BUDGET",
                "BUDGETED_FEES_ARE_NOT_CONFIRMED_CHARGES_AND_NO_FUNDS_ARE_RESERVED",
                "SIMULATION_INITIAL_CASH_MARGIN_FEE_AND_SLIPPAGE_ARE_NOT_BROKER_FACTS",
            ],
        }
        with write_transaction(self._engine) as connection:
            connection.execute(
                (sqlite_insert if connection.dialect.name == "sqlite" else pg_insert)(_budgets)
                .values(
                    budget_id=request_id,
                    stream_id=stream_id,
                    sequence=sequence,
                    query_id=query_id,
                    entry_id=entry_id,
                    recorded_at=now,
                    document=document,
                    sha256=_hash(document),
                )
                .on_conflict_do_nothing()
            )
        saved = self.get(request_id)
        if saved["request"] != request:
            raise ValueError("opening budget identity is already bound to different input")
        return saved

    def context(self, stream_id: UUID) -> dict[str, Any]:
        self._streams.get(stream_id)
        with self._engine.connect() as connection:
            ids = connection.scalars(
                select(_budgets.c.budget_id)
                .where(_budgets.c.stream_id == stream_id)
                .order_by(_budgets.c.recorded_at.desc())
                .limit(20)
            ).all()
        return {
            "budgets": [self.get(identifier) for identifier in ids],
        }

    def verify_all(self) -> int:
        count = 0
        with self._engine.connect().execution_options(yield_per=100) as connection:
            for identifier in connection.scalars(select(_budgets.c.budget_id)):
                self.get(identifier)
                count += 1
        return count
