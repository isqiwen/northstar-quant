"""Fix a flat account observation, then compare a later independent query.

This is not an external-fill ledger. The opening observation never changes and
never claims reconciliation; unexplained activity is shown, not applied as a
fabricated fill. Only saved, hash-verified broker queries supply account facts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Column,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from northstar_quant.broker.records import BrokerRecords
from northstar_quant.runtime import implementation_hash
from northstar_quant.strategy import decimal_text

_MONEY = (
    "Balance",
    "Available",
    "PreBalance",
    "PreMargin",
    "Deposit",
    "Withdraw",
    "CurrMargin",
    "FrozenMargin",
    "FrozenCash",
    "FrozenCommission",
    "CashIn",
    "Commission",
    "CloseProfit",
    "PositionProfit",
    "WithdrawQuota",
    "Reserve",
)
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
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("document", JSONB, nullable=False),
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
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("document", JSONB, nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("baseline_id", "query_batch_id"),
)


def initialize_broker_baselines(connection: Connection) -> None:
    """Add this Module's immutable records without replacing any account facts."""
    if connection.dialect.name != "postgresql":
        raise ValueError("broker baselines require PostgreSQL")
    _metadata.create_all(connection)
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


def _money(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise ValueError("money requires a bounded exact decimal string")
    try:
        number = Decimal(value)
        exponent = number.as_tuple().exponent
        if (
            not number.is_finite()
            or not isinstance(exponent, int)
            or exponent < -18
            or number.adjusted() > 33
            or len(number.as_tuple().digits) > 34
        ):
            raise ValueError("money is outside the bounded financial domain")
        return decimal_text(number)
    except ArithmeticError as error:
        raise ValueError("money is not a finite decimal") from error


def _facts(batch: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any], list[str]]:
    """Read observed fields, not an estimate of cash, P&L or missing holdings."""
    reasons = []
    complete = batch["completeness"]
    if batch["status"] != "COMPLETE" or complete["status"] != "COMPLETE":
        reasons.append("QUERY_NOT_COMPLETE")
    if complete["identity"] != "CONFIRMED":
        reasons.append("TD_ACCOUNT_IDENTITY_NOT_CONFIRMED")
    try:
        date.fromisoformat(complete["trading_day"])
    except (ValueError, TypeError):
        reasons.append("TRADING_DAY_UNKNOWN")
    sections = complete["sections"]
    activity = {}
    for name in _ACTIVITY:
        section = sections[name]
        activity[name] = section["rows"] if section["status"] == "COMPLETE" else None
        if activity[name] is None:
            reasons.append(f"{name.upper()}_NOT_COMPLETE")
    account = sections["account"]
    rows = account["rows"]
    funds: dict[str, str] = {}
    if account["status"] != "COMPLETE" or rows is None or len(rows) != 1:
        reasons.append("ONE_COMPLETE_CNY_ACCOUNT_REQUIRED")
    else:
        row = rows[0]
        if (
            row.get("BrokerID") != batch["profile"]["broker_id"]
            or row.get("AccountID") != batch["account_id"]
            or row.get("CurrencyID") != "CNY"
            or row.get("TradingDay") != complete["trading_day"]
        ):
            reasons.append("ACCOUNT_SCOPE_MISMATCH")
        for field in _MONEY:
            try:
                funds[field] = _money(row.get(field))
            except ValueError:
                reasons.append(f"ACCOUNT_{field.upper()}_UNKNOWN")
    capture = batch["capture"]
    if capture is None:
        reasons.append("CAPTURE_NOT_COMPLETE")
    elif any(event["callback"] in {"OnRtnTrade", "OnRtnOrder"} for event in capture["events"]):
        reasons.append("ACCOUNT_ACTIVITY_DURING_QUERY")
    return funds, activity, sorted(set(reasons))


def _eligibility(batch: dict[str, Any]) -> dict[str, Any]:
    funds, activity, reasons = _facts(batch)
    if any(activity[name] != [] for name in _ACTIVITY):
        reasons.append("FLAT_ACCOUNT_WITHOUT_ORDERS_OR_TRADES_REQUIRED")
    if any(field not in funds or Decimal(funds[field]) != 0 for field in _FLAT_ZERO):
        reasons.append("ZERO_MARGIN_FREEZES_AND_POSITION_PROFIT_REQUIRED")
    return {"allowed": not reasons, "reasons": sorted(set(reasons))}


class BrokerBaselines:
    """Immutable observation and comparison, with no network or rebase operation."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("broker baselines require PostgreSQL")
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
        funds, activity, _ = _facts(batch)
        document = {
            "baseline_id": str(request_id),
            "source_batch_id": str(source_batch_id),
            "recorded_at": now.isoformat().replace("+00:00", "Z"),
            "profile": batch["profile"],
            "account_id": batch["account_id"],
            "currency": "CNY",
            "trading_day": batch["completeness"]["trading_day"],
            "source_hash": _hash(batch),
            "implementation_hash": implementation_hash(),
            "opening": {"funds": funds, **activity},
            "status": "BASELINE_RECORDED",
            "scope": "FLAT_CNY_OBSERVATION",
            "execution": dict(_EXECUTION),
        }
        with self._engine.begin() as connection:
            connection.execute(
                insert(_baselines)
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
        observed, activity, reasons = _facts(batch)
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
            "implementation_hash": implementation_hash(),
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
        with self._engine.begin() as connection:
            connection.execute(
                insert(_checks)
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
