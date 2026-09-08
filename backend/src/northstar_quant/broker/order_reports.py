"""Explain reported orders against fixed, already deduplicated broker fills.

This is observation checking, not an order sender or a recoverable order manager.
Never infer a cancellation from absence, merge orders by OrderRef alone, or fill
a cumulative-trade gap using the observations being compared.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from northstar_quant.accounting.amounts import decimal_text

_STATES = {
    "0": "FILLED",
    "1": "PART_TRADED_QUEUEING",
    "2": "PART_TRADED_NOT_QUEUEING",
    "3": "NO_TRADE_QUEUEING",
    "4": "NO_TRADE_NOT_QUEUEING",
    "5": "CANCELED",
    "a": "UNKNOWN",
    "b": "NOT_TRIGGERED",
    "c": "TRIGGERED",
}
_SUBMITS = {
    "0": "INSERT_SUBMITTED",
    "1": "CANCEL_SUBMITTED",
    "2": "MODIFY_SUBMITTED",
    "3": "ACCEPTED",
    "4": "INSERT_REJECTED",
    "5": "CANCEL_REJECTED",
    "6": "MODIFY_REJECTED",
}


def _text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("missing order identity or terms")
    value = value.strip()
    if not value or any(ord(character) < 32 for character in value):
        raise ValueError("invalid order identity or terms")
    return value


def _lots(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        raise ValueError("invalid order quantity")
    return value


def _scope(source: dict[str, Any]) -> tuple[dict[str, Any], str, str | None]:
    if "stream_id" in source:
        binding = source["binding"]
        day = None
        for item in source["events"]:
            event = item["event"]
            if event["channel"] == "TD" and event["callback"] == "OnRspUserLogin":
                row = event["data"] or {}
                if (
                    not event["error_id"]
                    and row.get("UserID") == binding["account_id"]
                    and row.get("BrokerID") == binding["profile"]["broker_id"]
                ):
                    day = row.get("TradingDay")
        return binding["profile"], binding["account_id"], day
    return source["profile"], source["account_id"], source["completeness"]["trading_day"]


def order_key(exchange: str, identifier: str, batch: dict[str, Any]) -> str:
    scope = _scope(batch)
    return hashlib.sha256(
        json.dumps([*scope, exchange, identifier], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def decode_order(row: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    profile, account, trading_day = _scope(batch)
    if (row.get("BrokerID"), row.get("InvestorID"), row.get("TradingDay")) != (
        profile["broker_id"],
        account,
        trading_day,
    ):
        raise ValueError("order account or trading day differs")
    day = _text(row, "TradingDay")
    if re.fullmatch(r"[0-9]{8}", day) is None:
        raise ValueError("order trading day is invalid")
    date.fromisoformat(day)
    exchange, order_sys_id = _text(row, "ExchangeID"), _text(row, "OrderSysID")
    direction = {"0": "BUY", "1": "SELL"}.get(_text(row, "Direction"))
    offset, hedge = _text(row, "CombOffsetFlag"), _text(row, "CombHedgeFlag")
    if direction is None or len(offset) != 1 or len(hedge) != 1:
        raise ValueError("order direction or single-leg flags are not confirmed")
    original = _lots(row.get("VolumeTotalOriginal"))
    traded, remaining = _lots(row.get("VolumeTraded")), _lots(row.get("VolumeTotal"))
    if not original or traded + remaining != original:
        raise ValueError("order quantities do not conserve original volume")
    try:
        price = Decimal(_text(row, "LimitPrice"))
    except InvalidOperation as error:
        raise ValueError("order price is not decimal") from error
    exponent = price.as_tuple().exponent
    if (
        not price.is_finite()
        or price < 0
        or not isinstance(exponent, int)
        or exponent < -18
        or price.adjusted() > 33
        or len(price.as_tuple().digits) > 34
    ):
        raise ValueError("order price is outside exact financial bounds")
    status, submit = _text(row, "OrderStatus"), _text(row, "OrderSubmitStatus")
    state, submit_state = _STATES.get(status, "UNKNOWN"), _SUBMITS.get(submit, "UNKNOWN")
    problems = []
    active: bool | None = status in {"1", "3"} if status in {"0", "1", "2", "3", "4", "5"} else None
    if (
        status == "0"
        and (traded != original or remaining != 0)
        or status in {"1", "2"}
        and not 0 < traded < original
        or status in {"3", "4"}
        and traded != 0
        or status == "5"
        and traded >= original
    ):
        problems.append({"code": "ORDER_STATUS_QUANTITY_CONFLICT"})
        active = None
    if submit == "4":
        if status == "5" and traded == 0:
            state, active = "REJECTED", False
        else:
            problems.append({"code": "REJECTED_ORDER_HAS_CONFLICTING_STATE"})
            active = None
    elif submit not in {"1", "3", "5"}:
        problems.append({"code": "ORDER_SUBMISSION_UNRESOLVED"})
        active = None
    if status not in {"0", "1", "2", "3", "4", "5"}:
        problems.append({"code": "ORDER_STATE_UNRESOLVED"})
    if exchange != "SHFE" or offset not in {"0", "3", "4"} or hedge != "1":
        problems.append({"code": "ORDER_SCOPE_NOT_SUPPORTED"})
        active = None
    if "stream_id" in batch:
        instrument = batch["binding"]["terms"]
    else:
        instruments = batch["completeness"]["sections"]["instrument"]
        instrument = (
            instruments["rows"][0]
            if instruments["status"] == "COMPLETE" and len(instruments["rows"]) == 1
            else {}
        )
    if (
        instrument.get("ProductClass") != "1"
        or instrument.get("ExchangeID") != exchange
        or instrument.get("InstrumentID") != row.get("InstrumentID")
    ):
        problems.append({"code": "ORDER_INSTRUMENT_NOT_CONFIRMED"})
        active = None
    client = None
    if (
        type(row.get("FrontID")) is int
        and type(row.get("SessionID")) is int
        and row["FrontID"] >= 0
        and row["SessionID"] >= 0
        and isinstance(row.get("OrderRef"), str)
        and row["OrderRef"].strip()
    ):
        client = [row["FrontID"], row["SessionID"], _text(row, "OrderRef")]
    return {
        "order_id": order_key(exchange, order_sys_id, batch),
        "exchange": exchange,
        "symbol": _text(row, "InstrumentID").upper(),
        "order_sys_id": order_sys_id,
        "client_identity": client,
        "direction": direction,
        "offset_flag": offset,
        "hedge_flag": hedge,
        "limit_price": decimal_text(price),
        "order_price_type": _text(row, "OrderPriceType"),
        "time_condition": _text(row, "TimeCondition"),
        "volume_condition": _text(row, "VolumeCondition"),
        "min_volume": _lots(row.get("MinVolume")),
        "original_lots": original,
        "reported_traded_lots": traded,
        "reported_remaining_lots": remaining,
        "order_state": state,
        "submit_state": submit_state,
        "active": active,
        "problems": problems,
    }


def order_observations(batch: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if "stream_id" in batch:
        return [
            (
                {
                    "source_stream_id": batch["stream_id"],
                    "source_batch_id": batch["binding"]["request"]["query_batch_id"],
                    "sequence": event["sequence"],
                    "callback": event["callback"],
                    "received_at": event["received_at"],
                },
                event["data"],
            )
            for item in batch["events"]
            for event in (item["event"],)
            if event["sequence"] > batch["after_sequence"]
            and event["channel"] == "TD"
            and event["callback"] == "OnRtnOrder"
            and not event["error_id"]
            and event["data"] is not None
        ]
    section = batch["completeness"]["sections"]["orders"]
    result = []
    terminated = False
    for event in batch["capture"]["events"]:
        if event["channel"] != "TD":
            continue
        queried = (
            event["callback"] == "OnRspQryOrder"
            and not terminated
            and section["request_id"] is not None
            and event["request_id"] == section["request_id"]
        )
        if queried and event["is_last"] is True:
            terminated = True
        if (queried or event["callback"] == "OnRtnOrder") and not event["error_id"]:
            if event["data"] is not None:
                result.append(
                    (
                        {
                            "source_batch_id": batch["batch_id"],
                            "sequence": event["sequence"],
                            "callback": event["callback"],
                            "received_at": event["received_at"],
                        },
                        event["data"],
                    )
                )
    return result
