"""Interpret fixed receiver facts for the shared one-lot risk calculation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Engine

from northstar_quant.broker.market import ctp_quote_time
from northstar_quant.data_management.broker import verify_broker_contract
from northstar_quant.execution.orders import Side
from northstar_quant.market_data.sessions import SessionSchedule
from northstar_quant.risk import (
    OpeningAccount,
    OpeningCandidate,
    OpeningLimits,
    OpeningTerms,
    evaluate_opening_budget,
)


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


def calculate(
    engine: Engine,
    decision: dict[str, Any],
    entry: dict[str, Any],
    batch: dict[str, Any],
    price: Decimal,
    *,
    observed_at: datetime,
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
    desired = (abs(fraction) * config["risk"]["max_lots"]).to_integral_value(rounding=ROUND_FLOOR)
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
    if _at(batch["finished_at"]) > observed_at:
        raise ValueError("ACCOUNT_QUERY_NOT_AVAILABLE_AT_RISK")
    if entry["status"] != "READY":
        raise ValueError("POSITION_OR_ORDER_COMPARISON_NOT_MATCHED")
    if (
        entry["fill_count"] != 0
        or entry["position_projection"]["positions"]
        or any(
            batch["completeness"]["sections"][name]["rows"] != []
            for name in ("positions", "orders", "trades")
        )
    ):
        raise ValueError("FIRST_OPENING_REQUIRES_FLAT_ACCOUNT_WITHOUT_ACTIVITY")
    if batch["account_observation"]["account_activity_during_query"]:
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
        or _at(event["received_at"]) > _at(intent["generated_at"])
    ):
        raise ValueError("CONFIRMING_QUOTE_NOT_IDENTIFIED")
    ctp_quote_time(
        quote,
        schedule=(
            SessionSchedule.from_dict(binding["request"]["schedule"])
            if "schedule" in binding["request"]
            else None
        ),
    )
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
            max_lots=config["risk"]["max_lots"],
            max_gross_notional=_amount(config["risk"]["max_gross_notional"]),
            max_margin_fraction=_amount(config["risk"]["max_margin_fraction"]),
            max_adverse_price_move_fraction=_amount(
                config["risk"]["max_adverse_price_move_fraction"]
            ),
        ),
        candidate=OpeningCandidate(side=Side.BUY if fraction > 0 else Side.SELL, limit_price=price),
    )
