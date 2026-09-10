"""Fix a flat account observation, then compare a later independent query.

This is not an external-fill ledger. The opening observation never changes and
never claims reconciliation; unexplained activity is shown, not applied as a
fabricated fill. Only saved, hash-verified broker queries supply account facts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    Engine,
    ForeignKey,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from northstar_quant import code_revision
from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.broker.account_reports import account_baseline
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.persistence.sql import UTCDateTime, write_transaction

_FLAT_ZERO = ("CurrMargin", "FrozenMargin", "FrozenCash", "FrozenCommission", "PositionProfit")
_ACTIVITY = ("positions", "orders", "trades")
_EXECUTION = {"order_sending": False, "cancel_sending": False}
_metadata = MetaData()
_baselines = Table(
    "broker_account_baselines",
    _metadata,
    Column("baseline_id", PGUUID(as_uuid=True), primary_key=True),
    Column("profile_name", String(32), nullable=False),
    Column("account_id", String(12), nullable=False),
    Column("source_batch_id", PGUUID(as_uuid=True), nullable=False, unique=True),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("profile_name", "account_id"),
)
_checks = Table(
    "broker_baseline_checks",
    _metadata,
    Column("check_id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "baseline_id", PGUUID(as_uuid=True), ForeignKey(_baselines.c.baseline_id), nullable=False
    ),
    Column("query_batch_id", PGUUID(as_uuid=True), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("baseline_id", "query_batch_id"),
)


def initialize_broker_baselines(connection: Connection) -> None:
    """Add this Module's immutable records without replacing any account facts."""
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        for table_name in ("broker_account_baselines", "broker_baseline_checks"):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table_name}_{action} "
                    f"BEFORE {action} ON {table_name} "
                    "BEGIN SELECT RAISE(ABORT, 'Confirmed facts are immutable'); END"
                )
        return
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION broker_protect_baseline() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Broker baselines and comparisons are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in (_baselines, _checks):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable ON {table.name}")
        connection.exec_driver_sql(f"""
            CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table.name}
            FOR EACH ROW EXECUTE FUNCTION broker_protect_baseline()
        """)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() != UTC.utcoffset(result):
        raise ValueError("broker observation times must be UTC")
    return result


def _eligibility(batch: dict[str, Any]) -> dict[str, Any]:
    funds, activity, reasons = account_baseline(batch)
    if any(activity[name] != [] for name in _ACTIVITY):
        reasons.append("FLAT_ACCOUNT_WITHOUT_ORDERS_OR_TRADES_REQUIRED")
    if any(field not in funds or Decimal(funds[field]) != 0 for field in _FLAT_ZERO):
        reasons.append("ZERO_MARGIN_FREEZES_AND_POSITION_PROFIT_REQUIRED")
    return {"allowed": not reasons, "reasons": sorted(set(reasons))}


class BrokerBaselines:
    """Immutable observation and comparison, with no network or rebase operation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._records = BrokerRecords(engine)

    def _load(self, table: Table, identifier: UUID) -> dict[str, Any]:
        if not isinstance(identifier, UUID):
            raise ValueError("baseline commands require UUID identities")
        primary = list(table.primary_key)[0]
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(table).where(primary == identifier))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("broker baseline or comparison not found")
        document = row["document"]
        if (
            not isinstance(document, dict)
            or _hash(document) != row["sha256"]
            or document.get(primary.name) != str(identifier)
        ):
            raise ValueError("broker baseline or comparison evidence is damaged")
        # Indexed lookup identities must agree with the hashed document too;
        # integrity checking is not limited to the JSON payload alone.
        if table is _baselines:
            matches = (
                document.get("source_batch_id") == str(row["source_batch_id"])
                and document.get("account_id") == row["account_id"]
                and document.get("profile", {}).get("name") == row["profile_name"]
                and _time(document["recorded_at"]) == row["created_at"]
            )
        else:
            matches = (
                document.get("baseline_id") == str(row["baseline_id"])
                and document.get("query_batch_id") == str(row["query_batch_id"])
                and _time(document["created_at"]) == row["created_at"]
            )
        if not matches:
            raise ValueError("broker record identity differs from its fixed evidence")
        return document

    def get_baseline(self, baseline_id: UUID) -> dict[str, Any]:
        document = self._load(_baselines, baseline_id)
        source = self._records.get(UUID(document["source_batch_id"]))
        if _hash(source) != document["source_hash"]:
            raise ValueError("baseline source differs from its fixed evidence")
        return document

    def get_check(self, check_id: UUID) -> dict[str, Any]:
        document = self._load(_checks, check_id)
        baseline = self.get_baseline(UUID(document["baseline_id"]))
        query = self._records.get(UUID(document["query_batch_id"]))
        if _hash(baseline) != document["baseline_hash"] or _hash(query) != document["query_hash"]:
            raise ValueError("comparison inputs differ from their fixed evidence")
        return document

    def establish(self, source_batch_id: UUID, *, request_id: UUID) -> dict[str, Any]:
        try:
            saved = self.get_baseline(request_id)
        except LookupError:
            pass
        else:
            if saved["source_batch_id"] != str(source_batch_id):
                raise ValueError("baseline command is already bound to another query")
            return saved
        batch: dict[str, Any] = self._records.get(source_batch_id)
        eligibility = _eligibility(batch)
        if not eligibility["allowed"]:
            raise ValueError("cannot establish baseline: " + ", ".join(eligibility["reasons"]))
        now = datetime.now(UTC)
        if _time(batch["capture"]["finished_at"]) >= now:
            raise ValueError("baseline requires an already finished observation")
        funds, activity, _ = account_baseline(batch)
        document = {
            "baseline_id": str(request_id),
            "source_batch_id": str(source_batch_id),
            "recorded_at": now.isoformat().replace("+00:00", "Z"),
            "profile": batch["profile"],
            "account_id": batch["account_id"],
            "currency": "CNY",
            "trading_day": batch["completeness"]["trading_day"],
            "source_hash": _hash(batch),
            "code_revision": code_revision(),
            "opening": {"funds": funds, **activity},
            "status": "BASELINE_RECORDED",
            "scope": "FLAT_CNY_OBSERVATION",
            "execution": dict(_EXECUTION),
        }
        with write_transaction(self._engine) as connection:
            connection.execute(
                (sqlite_insert if connection.dialect.name == "sqlite" else pg_insert)(_baselines)
                .values(
                    baseline_id=request_id,
                    profile_name=batch["profile"]["name"],
                    account_id=batch["account_id"],
                    source_batch_id=source_batch_id,
                    created_at=now,
                    document=document,
                    sha256=_hash(document),
                )
                .on_conflict_do_nothing()
            )
        try:
            saved = self.get_baseline(request_id)
        except LookupError as error:
            raise ValueError(
                "this account already has a fixed baseline; it cannot be replaced"
            ) from error
        if saved["source_batch_id"] != str(source_batch_id):
            raise ValueError("baseline command is already bound to another query")
        return saved

    def compare(
        self, baseline_id: UUID, query_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, Any]:
        try:
            saved = self.get_check(request_id)
        except LookupError:
            pass
        else:
            if (saved["baseline_id"], saved["query_batch_id"]) != (
                str(baseline_id),
                str(query_batch_id),
            ):
                raise ValueError("comparison command is already bound to other inputs")
            return saved
        baseline = self.get_baseline(baseline_id)
        batch: dict[str, Any] = self._records.get(query_batch_id)
        if baseline["source_batch_id"] == str(query_batch_id):
            raise ValueError("a baseline cannot be compared with its own source query")
        if batch["profile"] != baseline["profile"] or batch["account_id"] != baseline["account_id"]:
            raise ValueError("comparison must use the same broker environment and account")
        capture = batch["capture"]
        recorded = _time(baseline["recorded_at"])
        if _time(batch["created_at"]) <= recorded or (
            capture is not None and _time(capture["started_at"]) <= recorded
        ):
            raise ValueError(
                "comparison requires an independent query begun after fixing the baseline"
            )
        if capture is None:
            # Its result can still change from PENDING to final. Never seal a
            # comparison to an input that is not itself immutable yet.
            raise ValueError("wait for the later query to finish before saving a comparison")
        if _time(capture["finished_at"]) >= datetime.now(UTC):
            raise ValueError("comparison query must already have finished")
        observed, activity, reasons = account_baseline(batch)
        if batch["completeness"]["trading_day"] != baseline["trading_day"]:
            reasons.append("DIFFERENT_TRADING_DAY_REQUIRES_SETTLEMENT_FACTS")
        fields = []
        differences = any(activity[name] for name in _ACTIVITY)
        with localcontext() as context:
            context.prec = 96
            for field, expected in baseline["opening"]["funds"].items():
                actual = observed.get(field)
                delta = None if actual is None else Decimal(actual) - Decimal(expected)
                differences = differences or delta is not None and delta != 0
                fields.append(
                    {
                        "field": field,
                        "expected": expected,
                        "observed": actual,
                        "delta": None if delta is None else decimal_text(delta),
                    }
                )
        status = "UNKNOWN" if reasons else "DIFFERENCES" if differences else "MATCHED"
        now = datetime.now(UTC)
        document = {
            "check_id": str(request_id),
            "baseline_id": str(baseline_id),
            "query_batch_id": str(query_batch_id),
            "baseline_hash": _hash(baseline),
            "query_hash": _hash(batch),
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "code_revision": code_revision(),
            "status": status,
            "scope": "BASELINE_COMPARISON_ONLY",
            "funds": fields,
            "activity": activity,
            "reasons": sorted(set(reasons)),
            "reconciliation": "UNRECONCILED",
            "limitations": [
                "NO_EXTERNAL_FILL_CASHFLOW_OR_SETTLEMENT_LEDGER",
                "QUERIES_ARE_NOT_ATOMIC_ACCOUNT_SNAPSHOTS",
                "OBSERVATIONS_DO_NOT_PROVE_CONTINUOUS_COVERAGE_OR_CURRENT_STATE",
                "FIELD_CHANGES_ARE_NOT_ATTRIBUTED_PNL",
            ],
            "execution": dict(_EXECUTION),
        }
        with write_transaction(self._engine) as connection:
            connection.execute(
                (sqlite_insert if connection.dialect.name == "sqlite" else pg_insert)(_checks)
                .values(
                    check_id=request_id,
                    baseline_id=baseline_id,
                    query_batch_id=query_batch_id,
                    created_at=now,
                    document=document,
                    sha256=_hash(document),
                )
                .on_conflict_do_nothing()
            )
        try:
            saved = self.get_check(request_id)
        except LookupError as error:
            raise ValueError(
                "this query already has a fixed comparison; read its existing record"
            ) from error
        if (saved["baseline_id"], saved["query_batch_id"]) != (
            str(baseline_id),
            str(query_batch_id),
        ):
            raise ValueError("comparison command is already bound to other inputs")
        return saved

    def context(self, query_batch_id: UUID) -> dict[str, Any]:
        batch: dict[str, Any] = self._records.get(query_batch_id)
        with self._engine.connect() as connection:
            baseline_id = connection.scalar(
                select(_baselines.c.baseline_id).where(
                    _baselines.c.profile_name == batch["profile"]["name"],
                    _baselines.c.account_id == batch["account_id"],
                )
            )
            checks = (
                []
                if baseline_id is None
                else list(
                    connection.scalars(
                        select(_checks.c.check_id)
                        .where(_checks.c.baseline_id == baseline_id)
                        .order_by(_checks.c.created_at.desc())
                        .limit(20)
                    )
                )
            )
        return {
            "eligibility": _eligibility(batch),
            "baseline": None if baseline_id is None else self.get_baseline(baseline_id),
            "checks": [self.get_check(identifier) for identifier in checks],
        }

    def verify_all(self) -> dict[str, int]:
        """Verify immutable records and their retained inputs, without recomputing them."""
        counts = {"baselines_count": 0, "checks_count": 0}
        with self._engine.connect() as connection:
            for table, reader, name in (
                (_baselines, self.get_baseline, "baselines_count"),
                (_checks, self.get_check, "checks_count"),
            ):
                for identifier in connection.scalars(select(list(table.primary_key)[0])).yield_per(
                    100
                ):
                    reader(identifier)
                    counts[name] += 1
        return counts
