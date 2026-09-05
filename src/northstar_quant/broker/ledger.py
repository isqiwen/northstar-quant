"""Append confirmed trades, then compare frozen gross positions independently.

One bounded, same-day position ledger starts at an existing flat observation.
Entries store only newly identified fills; previous entries and raw queries are
retained by hash. Cash, fees, settlement and sending authority are not inferred.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from northstar_quant.accounting import PositionChange, project_intraday_positions
from northstar_quant.broker.baselines import BrokerBaselines
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.data.broker import resolve_broker_contract, verify_broker_contract
from northstar_quant.runtime import implementation_hash
from northstar_quant.strategy import decimal_text

_metadata = MetaData()
_entries = Table(
    "broker_position_entries",
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
_checks = Table(
    "broker_position_checks",
    _metadata,
    Column("check_id", PGUUID(as_uuid=True), primary_key=True),
    Column("entry_id", PGUUID(as_uuid=True), nullable=False),
    Column("query_batch_id", PGUUID(as_uuid=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("document", JSONB, nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("entry_id", "query_batch_id"),
)
_EXECUTION = {"order_sending": False, "cancel_sending": False}
_MAX_ENTRIES = 1000
_MAX_FILLS = 10000


def initialize_broker_ledger(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        raise ValueError("broker position ledger requires PostgreSQL")
    _metadata.create_all(connection)
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


def _string(row: dict[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("trade identity or field is missing")
    value = value.strip()
    if not value or any(ord(character) < 32 for character in value):
        raise ValueError("trade identity or field is invalid")
    return value


def _quantity(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        raise ValueError("position quantity must be a bounded nonnegative integer")
    return value


def _trade(row: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    if (row.get("BrokerID"), row.get("InvestorID")) != (
        batch["profile"]["broker_id"],
        batch["account_id"],
    ):
        raise ValueError("trade identity differs from account")
    day = _string(row, "TradingDay")
    date.fromisoformat(day)
    trade_date, trade_time = _string(row, "TradeDate"), _string(row, "TradeTime")
    if (
        re.fullmatch(r"[0-9]{8}", day) is None
        or re.fullmatch(r"[0-9]{8}", trade_date) is None
        or re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}", trade_time) is None
    ):
        raise ValueError("trade requires the reported CTP calendar date and local time")
    filled_at = datetime.combine(
        date.fromisoformat(trade_date), time.fromisoformat(trade_time), ZoneInfo("Asia/Shanghai")
    ).astimezone(UTC)
    exchange, trade_id = _string(row, "ExchangeID"), _string(row, "TradeID")
    direction = {"0": "BUY", "1": "SELL"}.get(_string(row, "Direction"))
    if direction is None:
        raise ValueError("trade direction is unknown")
    quantity = _quantity(row.get("Volume"))
    if not quantity:
        raise ValueError("trade quantity must be positive")
    raw_price = _string(row, "Price")
    try:
        price = Decimal(raw_price)
        exponent = price.as_tuple().exponent
        if (
            not price.is_finite()
            or price <= 0
            or not isinstance(exponent, int)
            or exponent < -18
            or price.adjusted() > 33
            or len(price.as_tuple().digits) > 34
        ):
            raise ValueError("trade price is outside the exact financial domain")
    except ArithmeticError as error:
        raise ValueError("trade price is invalid") from error
    offset = _string(row, "OffsetFlag")
    identity = [batch["profile"], batch["account_id"], day, exchange, trade_id, direction]
    return {
        "fill_id": _hash(identity),
        "exchange": exchange,
        "symbol": _string(row, "InstrumentID").upper(),
        "trade_id": trade_id,
        "order_sys_id": _string(row, "OrderSysID"),
        "direction": direction,
        "offset": {"0": "OPEN", "3": "CLOSE_TODAY", "4": "CLOSE_YESTERDAY"}.get(
            offset, "UNSUPPORTED"
        ),
        "offset_flag": offset,
        "hedge_flag": _string(row, "HedgeFlag"),
        "price": decimal_text(price),
        "quantity_lots": quantity,
        "trading_day": day,
        "trade_date": trade_date,
        "trade_time": trade_time,
        "filled_at": filled_at.isoformat().replace("+00:00", "Z"),
        "fee": None,
    }


def _economic(fill: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in fill.items()
        if key not in {"contract_id", "source_batch_id", "source_sequence"}
    }


def _observed_trades(batch: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    section = batch["completeness"]["sections"]["trades"]
    result = []
    terminated = False
    for event in batch["capture"]["events"]:
        queried = (
            event["callback"] == "OnRspQryTrade"
            and not terminated
            and section["request_id"] is not None
            and event["request_id"] == section["request_id"]
        )
        if event["channel"] != "TD":
            continue
        if queried and event["is_last"] is True:
            terminated = True
        if (queried or event["callback"] == "OnRtnTrade") and not event["error_id"]:
            if event["data"] is not None:
                result.append((event["sequence"], event["data"]))
    return result


def _problems(batch: dict[str, Any], day: str) -> list[dict[str, Any]]:
    reasons = []
    if batch["status"] != "COMPLETE":
        reasons.append("QUERY_NOT_COMPLETE")
    if batch["completeness"]["identity"] != "CONFIRMED":
        reasons.append("TD_ACCOUNT_IDENTITY_NOT_CONFIRMED")
    if batch["completeness"]["trading_day"] != day:
        reasons.append("SETTLEMENT_AND_NEW_TRADING_DAY_NOT_SUPPORTED")
    return [{"code": reason, "source_batch_id": batch["batch_id"]} for reason in reasons]


class BrokerLedger:
    """A same-day append-only position book, never a cash or execution authority."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("broker position ledger requires PostgreSQL")
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
            for fill in entry["added_fills"]:
                if fill["contract_id"] is not None:
                    instrument = source["completeness"]["sections"]["instrument"]["rows"][0]
                    contract = verify_broker_contract(
                        self._engine, UUID(fill["contract_id"]), instrument
                    )
                    if (contract.exchange, contract.symbol) != (fill["exchange"], fill["symbol"]):
                        raise ValueError("position fill differs from its canonical contract")
            history.append(entry)
        return history

    def get(self, entry_id: UUID) -> dict[str, Any]:
        entry = self._raw(_entries, entry_id)
        history = self._history(UUID(entry["baseline_id"]), through=entry["ordinal"])
        if not history or history[-1] != entry:
            raise ValueError("position ledger entry is outside its fixed chain")
        return entry

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
        # Serialize both the deduplication set and its resulting projection.
        lock_key = int.from_bytes(
            hashlib.sha256(baseline_id.bytes + b"positions").digest()[:8], "big", signed=True
        )
        with self._engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            try:
                saved = self.get(request_id)
            except LookupError:
                pass
            else:
                if (saved["baseline_id"], saved["source_batch_id"]) != (
                    str(baseline_id),
                    str(source_batch_id),
                ):
                    raise ValueError("position command is already bound to different inputs")
                return saved
            baseline = self._baselines.get_baseline(baseline_id)
            batch: dict[str, Any] = self._records.get(source_batch_id)
            if (
                batch["profile"] != baseline["profile"]
                or batch["account_id"] != baseline["account_id"]
            ):
                raise ValueError("position ledger requires the same environment and account")
            self._after(batch, baseline["recorded_at"])
            history = self._history(baseline_id)
            if len(history) >= _MAX_ENTRIES:
                raise ValueError("position ledger reached its bounded daily entry limit")
            if any(item["source_batch_id"] == str(source_batch_id) for item in history):
                raise ValueError("query already has a fixed ledger entry; read it instead")
            previous = history[-1] if history else None
            if previous is not None:
                prior: dict[str, Any] = self._records.get(UUID(previous["source_batch_id"]))
                if _time(batch["created_at"]) <= _time(prior["capture"]["finished_at"]):
                    raise ValueError("ledger ingestion requires ordered non-overlapping queries")
            known = {fill["fill_id"]: fill for item in history for fill in item["added_fills"]}
            previous_fill_ids = set(known)
            queried_fill_ids = set()
            queried_sequences = {
                event["sequence"]
                for event in batch["capture"]["events"]
                if event["callback"] == "OnRspQryTrade"
            }
            problems = [problem for item in history for problem in item["new_problems"]]
            new_problems = _problems(batch, baseline["trading_day"])
            added, duplicate_count = [], 0
            instruments = batch["completeness"]["sections"]["instrument"]
            contract = None
            for sequence, row in _observed_trades(batch):
                locator = {"source_batch_id": str(source_batch_id), "sequence": sequence}
                try:
                    fill = _trade(row, batch)
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
                    if (
                        instruments["status"] != "COMPLETE"
                        or len(instruments["rows"]) != 1
                        or fill["exchange"] != "SHFE"
                        or fill["symbol"] != batch["instrument"].upper()
                    ):
                        raise ValueError("trade has no confirmed supported contract")
                    if contract is None:
                        contract = resolve_broker_contract(self._engine, instruments["rows"][0])
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
                known[fill["fill_id"]] = fill
                added.append(fill)
            if previous_fill_ids - queried_fill_ids:
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
                "implementation_hash": implementation_hash(),
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
        return self.get(request_id)

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
        for sequence, row in _observed_trades(batch):
            try:
                fill = _trade(row, batch)
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
            "implementation_hash": implementation_hash(),
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
        from sqlalchemy.dialects.postgresql import insert

        with self._engine.begin() as connection:
            connection.execute(
                insert(_checks)
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
                (item for item in history if item["source_batch_id"] == str(query_batch_id)), None
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
        return counts


def _compare_positions(
    entry: dict[str, Any], batch: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    problems: list[dict[str, Any]] = []
    expected = {
        (item["exchange"], item["symbol"], item["hedge_flag"], item["direction"]): item
        for item in entry["position_projection"]["positions"]
    }
    observed: dict[tuple[str, str, str, str], dict[str, int]] = {}
    section = batch["completeness"]["sections"]["positions"]
    complete = section["status"] == "COMPLETE"
    if not complete:
        problems.append({"code": "POSITIONS_NOT_COMPLETE"})
    seen = set()
    for row in section["rows"] or []:
        try:
            direction = {"2": "LONG", "3": "SHORT"}.get(row.get("PosiDirection"))
            if direction is None or row.get("ExchangeID") != "SHFE" or row.get("HedgeFlag") != "1":
                raise ValueError("unsupported position scope")
            if row.get("TradingDay") != batch["completeness"]["trading_day"]:
                raise ValueError("position day differs")
            key = ("SHFE", _string(row, "InstrumentID").upper(), "1", direction)
            age = {"1": "today", "2": "yesterday"}.get(row.get("PositionDate"))
            if age is None or (*key, age) in seen:
                raise ValueError("position age is unknown or repeated")
            seen.add((*key, age))
            quantity, today = _quantity(row.get("Position")), _quantity(row.get("TodayPosition"))
            _quantity(row.get("YdPosition"))
            if today != (quantity if age == "today" else 0):
                raise ValueError("position date and quantity disagree")
            observed.setdefault(key, {"today": 0, "yesterday": 0})[age] = quantity
        except ValueError:
            complete = False
            problems.append({"code": "POSITION_FIELDS_NOT_CONFIRMED"})
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
