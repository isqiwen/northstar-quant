"""A fixed shadow target and saved broker facts produce a one-lot opening budget.

This is not a sender, a reservation, a replayed historical decision or a current
account certificate. Risk owns the arithmetic; this Module owns evidence,
unsupported facts, immutable commands and their private workspace explanation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
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
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from northstar_quant.broker.ledger import BrokerLedger
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.streams import BrokerStreams
from northstar_quant.data.broker import verify_broker_contract
from northstar_quant.data.library import DataLibrary
from northstar_quant.data.live import ctp_day_quote_time
from northstar_quant.risk import (
    OpeningAccount,
    OpeningCandidate,
    OpeningLimits,
    OpeningTerms,
    Side,
    evaluate_opening_budget,
)
from northstar_quant.runtime import implementation_hash
from northstar_quant.strategy import decimal_text

_metadata = MetaData()
_budgets = Table(
    "broker_opening_budgets",
    _metadata,
    Column("budget_id", PGUUID(as_uuid=True), primary_key=True),
    Column("stream_id", PGUUID(as_uuid=True), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("order_check_id", PGUUID(as_uuid=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("document", JSONB, nullable=False),
    Column("sha256", String(64), nullable=False),
)


def initialize_opening_budgets(connection: Connection) -> None:
    _metadata.create_all(connection)
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


def _amount(value: object) -> Decimal:
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise ValueError("EXACT_FINANCIAL_FIELD_MISSING")
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
            raise ValueError("FINANCIAL_FIELD_OUTSIDE_SUPPORTED_RANGE")
        return number
    except ArithmeticError as error:
        raise ValueError("INVALID_FINANCIAL_FIELD") from error


def _at(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() != UTC.utcoffset(result):
        raise ValueError("BUDGET_EVIDENCE_REQUIRES_UTC")
    return result


def _one(batch: dict[str, Any], section: str) -> dict[str, Any]:
    part = batch["completeness"]["sections"][section]
    if part["status"] != "COMPLETE" or not isinstance(part["rows"], list) or len(part["rows"]) != 1:
        raise ValueError(f"ONE_COMPLETE_{section.upper()}_REQUIRED")
    return cast(dict[str, Any], part["rows"][0])


def _calculate(
    engine: Engine,
    decision: dict[str, Any],
    order: dict[str, Any],
    parent: dict[str, Any],
    entry: dict[str, Any],
    batch: dict[str, Any],
    price: Decimal,
) -> dict[str, object]:
    binding, result = decision["binding"], decision["result"]
    intent, bar = result["intent"], result["bar"]
    if not isinstance(intent, dict) or not isinstance(bar, dict) or result["reason"] is not None:
        raise ValueError("COMMITTED_SHADOW_TARGET_REQUIRED")
    if (
        intent["contract_id"] != binding["contract_id"]
        or bar["contract_id"] != binding["contract_id"]
    ):
        raise ValueError("TARGET_CONTRACT_MISMATCH")
    config = binding["configuration"]["config"]
    fraction = _amount(intent["target_fraction"])
    desired = (abs(fraction) * config["max_lots"]).to_integral_value(rounding=ROUND_FLOOR)
    if desired < 1:
        raise ValueError("TARGET_DOES_NOT_REQUEST_ONE_OPENING_LOT")
    if not Decimal(-1) <= fraction <= Decimal(1):
        raise ValueError("TARGET_FRACTION_OUTSIDE_SUPPORTED_RANGE")
    if (
        batch["status"] != "COMPLETE"
        or batch["completeness"]["status"] != "COMPLETE"
        or batch["completeness"]["identity"] != "CONFIRMED"
    ):
        raise ValueError("COMPLETE_IDENTITY_CONFIRMED_QUERY_REQUIRED")
    if batch["completeness"]["trading_day"] != bar["trading_day"]:
        raise ValueError("ACCOUNT_AND_TARGET_TRADING_DAY_DIFFER")
    if _at(batch["capture"]["finished_at"]) > _at(intent["generated_at"]):
        raise ValueError("ACCOUNT_QUERY_FINISHED_AFTER_TARGET")
    if order["status"] != "MATCHED" or parent["status"] != "MATCHED" or entry["status"] != "READY":
        raise ValueError("POSITION_OR_ORDER_COMPARISON_NOT_MATCHED")
    if (
        entry["fill_count"] != 0
        or entry["position_projection"]["positions"]
        or order["orders"]
        or parent["unrecorded_fills"]
        or any(
            batch["completeness"]["sections"][name]["rows"] != []
            for name in ("positions", "orders", "trades")
        )
    ):
        raise ValueError("FIRST_OPENING_REQUIRES_FLAT_ACCOUNT_WITHOUT_ACTIVITY")
    if any(
        event["callback"] in {"OnRtnTrade", "OnRtnOrder"} for event in batch["capture"]["events"]
    ):
        raise ValueError("ACCOUNT_ACTIVITY_DURING_QUERY")
    funds = _one(batch, "account")
    if (
        funds.get("BrokerID") != binding["profile"]["broker_id"]
        or funds.get("AccountID") != binding["account_id"]
        or funds.get("TradingDay") != bar["trading_day"]
        or funds.get("CurrencyID") != "CNY"
        or funds.get("BizType") != "1"
    ):
        raise ValueError("CNY_FUTURES_ACCOUNT_SCOPE_NOT_CONFIRMED")
    for field in ("CurrMargin", "FrozenMargin", "FrozenCash", "FrozenCommission", "PositionProfit"):
        if _amount(funds.get(field)) != 0:
            raise ValueError("FIRST_OPENING_REQUIRES_ZERO_MARGIN_FREEZES_AND_POSITION_PROFIT")
    instrument = _one(batch, "instrument")
    verify_broker_contract(engine, UUID(binding["contract_id"]), instrument)
    if instrument.get("InstrumentID") != binding["instrument"]:
        raise ValueError("EXACT_INSTRUMENT_REQUIRED")
    if type(instrument.get("IsTrading")) is not int or instrument["IsTrading"] != 1:
        raise ValueError("INSTRUMENT_TRADING_STATUS_NOT_CONFIRMED")
    margin, fee = _one(batch, "margin"), _one(batch, "commission")
    for row in (margin, fee):
        if (
            row.get("BrokerID") != binding["profile"]["broker_id"]
            or row.get("InvestorID") != binding["account_id"]
            or row.get("InstrumentID") != binding["instrument"]
            or row.get("ExchangeID") != "SHFE"
            or row.get("InvestorRange") != "3"
            or row.get("InvestUnitID") != ""
        ):
            raise ValueError("ACCOUNT_SPECIFIC_FEE_OR_MARGIN_SCOPE_NOT_CONFIRMED")
    if (
        margin.get("HedgeFlag") != "1"
        or type(margin.get("IsRelative")) is not int
        or margin["IsRelative"] != 0
    ):
        raise ValueError("ABSOLUTE_SPECULATION_MARGIN_REQUIRED")
    if fee.get("BizType") != "1":
        raise ValueError("FUTURES_COMMISSION_SCOPE_NOT_CONFIRMED")
    event, quote = decision["event"], decision["event"]["data"]
    if (
        event["channel"] != "MD"
        or event["callback"] != "OnRtnDepthMarketData"
        or not isinstance(quote, dict)
        or quote.get("InstrumentID") != binding["instrument"]
        or quote.get("TradingDay") != bar["trading_day"]
        or quote.get("ActionDay") != bar["trading_day"]
        or _at(event["received_at"]) > _at(intent["generated_at"])
    ):
        raise ValueError("CONFIRMING_DAY_QUOTE_NOT_IDENTIFIED")
    return evaluate_opening_budget(
        account=OpeningAccount(
            equity=_amount(funds.get("Balance")),
            available=_amount(funds.get("Available")),
            current_margin=_amount(funds.get("CurrMargin")),
        ),
        terms=OpeningTerms(
            price_tick=_amount(instrument.get("PriceTick")),
            multiplier=Decimal(instrument["VolumeMultiple"]),
            long_margin_by_money=_amount(margin.get("LongMarginRatioByMoney")),
            long_margin_by_volume=_amount(margin.get("LongMarginRatioByVolume")),
            short_margin_by_money=_amount(margin.get("ShortMarginRatioByMoney")),
            short_margin_by_volume=_amount(margin.get("ShortMarginRatioByVolume")),
            open_fee_by_money=_amount(fee.get("OpenRatioByMoney")),
            open_fee_by_volume=_amount(fee.get("OpenRatioByVolume")),
            lower_limit=_amount(quote.get("LowerLimitPrice")),
            upper_limit=_amount(quote.get("UpperLimitPrice")),
            pre_settlement_price=_amount(quote.get("PreSettlementPrice")),
            last_price=_amount(quote.get("LastPrice")),
            min_limit_lots=cast(int, instrument.get("MinLimitOrderVolume")),
            max_limit_lots=cast(int, instrument.get("MaxLimitOrderVolume")),
        ),
        limits=OpeningLimits(
            max_lots=config["max_lots"],
            max_gross_notional=_amount(config["max_gross_notional"]),
            max_margin_fraction=_amount(config["max_margin_fraction"]),
            max_adverse_price_move_fraction=_amount(config["max_adverse_price_move_fraction"]),
        ),
        candidate=OpeningCandidate(side=Side.BUY if fraction > 0 else Side.SELL, limit_price=price),
    )


class BrokerOpeningBudgets:
    """Persist a non-executable budget from fixed references, never operator account state."""

    def __init__(self, engine: Engine, library: DataLibrary) -> None:
        self._engine, self._library = engine, library
        self._streams = BrokerStreams(engine, library)
        self._ledger, self._records = BrokerLedger(engine), BrokerRecords(engine)

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
            or document["order_check_id"] != str(row["order_check_id"])
            or _at(document["recorded_at"]) != row["recorded_at"]
        ):
            raise ValueError("opening budget evidence is damaged")
        try:
            decision = self._streams.decision(UUID(document["stream_id"]), document["sequence"])
            order = self._ledger.get_order_check(UUID(document["order_check_id"]))
            query = self._records.get(UUID(document["query_batch_id"]))
        except LookupError as error:
            raise ValueError("opening budget source evidence is missing") from error
        if (
            decision != document["inputs"]["decision"]
            or _hash(order) != document["inputs"]["order_check_hash"]
            or order["query_batch_id"] != document["query_batch_id"]
            or _hash(query) != document["inputs"]["query_hash"]
        ):
            raise ValueError("opening budget source differs from its fixed evidence")
        return cast(dict[str, Any], document)

    def create(
        self,
        stream_id: UUID,
        sequence: int,
        order_check_id: UUID,
        *,
        limit_price: Decimal,
        request_id: UUID,
    ) -> dict[str, Any]:
        if not all(isinstance(value, UUID) for value in (stream_id, order_check_id, request_id)):
            raise ValueError("opening budgets require UUID identities")
        if type(sequence) is not int or not 1 <= sequence <= 100_000:
            raise ValueError("opening budget requires one retained decision sequence")
        if not isinstance(limit_price, Decimal) or not limit_price.is_finite() or limit_price <= 0:
            raise ValueError("opening limit price must be a positive exact decimal")
        price = _amount(str(limit_price))
        request = {
            "stream_id": str(stream_id),
            "sequence": sequence,
            "order_check_id": str(order_check_id),
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
        order = self._ledger.get_order_check(order_check_id)
        parent = self._ledger.get_check(UUID(order["position_check_id"]))
        entry = self._ledger.get(UUID(parent["entry_id"]))
        batch: dict[str, Any] = self._records.get(UUID(parent["query_batch_id"]))
        binding = decision["binding"]
        if any(batch[key] != binding[key] for key in ("profile", "account_id", "instrument")):
            raise ValueError("opening budget requires the same environment, account and instrument")
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
            if (
                batch["capture"] is not None
                and (
                    _at(intent["generated_at"]) - _at(batch["capture"]["started_at"])
                ).total_seconds()
                > 5
            ):
                blockers.append("ACCOUNT_QUERY_NOT_CURRENT_AT_TARGET")
        try:
            source_time = ctp_day_quote_time(decision["event"]["data"] or {})
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
            budget = _calculate(self._engine, decision, order, parent, entry, batch, price)
            status, reasons = budget["outcome"], budget["reasons"]
        except ValueError as error:
            status, reasons = "UNKNOWN", [str(error)]
        parts = batch["completeness"]["sections"]
        document = {
            "budget_id": str(request_id),
            **request,
            "query_batch_id": parent["query_batch_id"],
            "request": request,
            "recorded_at": now.isoformat(),
            "implementation_hash": implementation_hash(),
            "status": status,
            "reasons": reasons,
            "budget": budget,
            "execution_blockers": blockers,
            "execution": {"order_sending": False, "cancel_sending": False},
            "scope": "SAVED_SHADOW_FIRST_OPENING_BUDGET_ONLY",
            "inputs": {
                "decision": decision,
                "market_source_time": None if source_time is None else source_time.isoformat(),
                "order_check_hash": _hash(order),
                "position_check_id": parent["check_id"],
                "position_check_hash": _hash(parent),
                "entry_id": entry["entry_id"],
                "entry_hash": _hash(entry),
                "query_hash": _hash(batch),
                "query_window": None
                if batch["capture"] is None
                else {key: batch["capture"][key] for key in ("started_at", "finished_at")},
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
        with self._engine.begin() as connection:
            connection.execute(
                insert(_budgets)
                .values(
                    budget_id=request_id,
                    stream_id=stream_id,
                    sequence=sequence,
                    order_check_id=order_check_id,
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
        stream = self._streams.get(stream_id)
        binding = cast(dict[str, Any], stream["binding"])
        ledger = self._ledger.context(UUID(binding["request"]["query_batch_id"]))
        with self._engine.connect() as connection:
            ids = connection.scalars(
                select(_budgets.c.budget_id)
                .where(_budgets.c.stream_id == stream_id)
                .order_by(_budgets.c.recorded_at.desc())
                .limit(20)
            ).all()
        return {
            "budgets": [self.get(identifier) for identifier in ids],
            "order_checks": ledger["order_checks"],
        }

    def verify_all(self) -> int:
        count = 0
        with self._engine.connect().execution_options(yield_per=100) as connection:
            for identifier in connection.scalars(select(_budgets.c.budget_id)):
                self.get(identifier)
                count += 1
        return count
