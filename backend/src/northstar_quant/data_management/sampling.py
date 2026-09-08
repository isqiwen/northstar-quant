"""Observed-minute reconstruction shared by retained Data processing and Live inputs."""

import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.broker.market import DAY, FRESH, SHANGHAI, decode_quote
from northstar_quant.broker.records import BrokerEvent


def sample_market(
    state: dict[str, Any],
    event: BrokerEvent,
    *,
    instrument: str,
    contract_id: UUID,
    price_tick: Decimal,
    now: datetime,
) -> dict[str, Any]:
    """Sample one copied event without I/O, decisions or synthesizing missing minutes.

    Source and receipt clocks are explicit. Initial/post-break minutes are partial;
    completion waits for its causal confirmation and never flushes terminal bars.
    Only one caller-verified SHFE DAY contract is supported in the current sampler.
    """
    if (
        not isinstance(instrument, str)
        or re.fullmatch(r"[A-Za-z]{1,3}[0-9]{4}", instrument) is None
        or not isinstance(contract_id, UUID)
        or not isinstance(price_tick, Decimal)
        or not price_tick.is_finite()
        or not Decimal("1e-18") <= price_tick < Decimal("1e18")
    ):
        raise ValueError("live market requires a fixed SHFE futures contract and positive tick")
    if now.utcoffset() != timedelta(0):
        raise ValueError("live processing time must be explicit UTC")
    binding = {
        "instrument": instrument.upper(),
        "contract_id": str(contract_id),
        "price_tick": decimal_text(price_tick),
    }
    if state and state.get("binding") != binding:
        raise ValueError("live market checkpoint cannot change its fixed binding")
    result: dict[str, Any] = (
        deepcopy(state)
        if state
        else {
            "binding": binding,
            "status": "WARMING_UP",
            "reason": "WAITING_FOR_QUOTE",
            "quality": "WAITING",
            "trading_day": None,
            "last_sequence": 0,
            "last_event_hash": None,
            "last_quote": None,
            "current_minute": None,
            "pending_completed_bar": None,
        }
    )
    result["completed_bar"] = None
    if result["status"] == "HALTED":
        return result
    event_hash = _hash(event.to_dict())
    if event.sequence <= result["last_sequence"]:
        if event.sequence == result["last_sequence"] and event_hash == result["last_event_hash"]:
            result.update(quality="DUPLICATE", reason="DUPLICATE_EVENT")
            return result
        return _halt(result, "EVENT_SEQUENCE_REGRESSION")
    result.update(last_sequence=event.sequence, last_event_hash=event_hash)
    if event.callback == "OnFrontDisconnected":
        return _halt(result, "DISCONNECTED")
    if event.error_id:
        return _halt(result, "BROKER_ERROR")
    if event.callback != "OnRtnDepthMarketData":
        return result
    if event.channel != "MD":
        return _halt(result, "UNEXPECTED_MARKET_CHANNEL")
    try:
        quote = decode_quote(event, instrument, price_tick, now)
    except ValueError as error:
        return _halt(result, str(error))
    if result["trading_day"] not in (None, quote["trading_day"]):
        return _halt(result, "TRADING_DAY_CHANGED")
    previous = result["last_quote"]
    if previous is not None:
        if _at(quote["received_at"]) < _at(previous["received_at"]):
            return _halt(result, "RECEIPT_TIME_REGRESSION")
        if quote["content_hash"] == previous["content_hash"]:
            result.update(quality="DUPLICATE", reason="DUPLICATE_QUOTE")
            return result
        elapsed = _at(quote["event_time"]) - _at(previous["event_time"])
        if elapsed < timedelta(0):
            return _halt(result, "LATE_OR_REVISED_QUOTE")
        if quote["cumulative_volume"] < previous["cumulative_volume"]:
            return _halt(result, "CUMULATIVE_VOLUME_DECREASED")
        if elapsed == timedelta(0) and quote["cumulative_volume"] == previous["cumulative_volume"]:
            return _halt(result, "AMBIGUOUS_SAME_TIME_QUOTE")
        if (
            quote["cumulative_volume"] == previous["cumulative_volume"]
            and quote["price"] != previous["price"]
        ):
            return _halt(result, "PRICE_CHANGED_WITHOUT_VOLUME")
        if quote["segment"] == previous["segment"] and elapsed > FRESH:
            return _halt(result, "SOURCE_GAP")
        if quote["segment"] != previous["segment"]:
            day = date.fromisoformat(quote["trading_day"])
            previous_end = datetime.combine(day, DAY[previous["segment"]][1], SHANGHAI)
            next_start = datetime.combine(day, DAY[quote["segment"]][0], SHANGHAI)
            if (
                quote["segment"] != previous["segment"] + 1
                or previous_end - _at(previous["event_time"]) > FRESH
                or _at(quote["event_time"]) - next_start > FRESH
            ):
                return _halt(result, "SOURCE_GAP")
    result.update(trading_day=quote["trading_day"], last_quote=quote, quality="ACCEPTED")
    current = result["current_minute"]
    if previous is None or current is None or previous["segment"] != quote["segment"]:
        result.update(
            current_minute=_minute(quote, partial=True, delta=None),
            pending_completed_bar=None,
            status="WARMING_UP",
            reason="INITIAL_PARTIAL_MINUTE" if previous is None else "SESSION_BREAK_REWARM",
        )
        return result
    delta = quote["cumulative_volume"] - previous["cumulative_volume"]
    if quote["minute_start"] == current["start_at"]:
        current.update(
            high=decimal_text(max(Decimal(current["high"]), Decimal(quote["price"]))),
            low=decimal_text(min(Decimal(current["low"]), Decimal(quote["price"]))),
            close=quote["price"],
            last_sequence=quote["sequence"],
            close_received_at=quote["received_at"],
            volume=None if current["partial"] else current["volume"] + delta,
        )
    else:
        if _at(quote["minute_start"]) - _at(current["start_at"]) != timedelta(minutes=1):
            return _halt(result, "MISSING_MINUTE")
        if not current["partial"]:
            completed = {key: value for key, value in current.items() if key != "partial"}
            completed.update(
                contract_id=str(contract_id),
                instrument=instrument.upper(),
                trading_day=quote["trading_day"],
                available_at=_iso(max(_at(current["completed_at"]), _at(quote["received_at"]))),
                confirmed_by_sequence=quote["sequence"],
                price_basis="OBSERVED_CTP_LAST_PRICE",
                volume_basis="CUMULATIVE_DELTA_AT_SNAPSHOT_TIME",
            )
            completed["observation_id"] = str(uuid5(contract_id, _hash(completed)))
            result["pending_completed_bar"] = completed
        result["current_minute"] = _minute(quote, partial=False, delta=delta)
    pending = result["pending_completed_bar"]
    if pending is not None and now >= _at(pending["available_at"]):
        result["pending_completed_bar"] = None
        result["completed_bar"] = pending
    result.update(
        reason="CLOCK_TOLERANCE_WAIT"
        if result["pending_completed_bar"] is not None
        else "OBSERVING_MINUTE",
    )
    return result


def _minute(quote: dict[str, Any], *, partial: bool, delta: int | None) -> dict[str, Any]:
    return {
        "start_at": quote["minute_start"],
        "completed_at": _iso(_at(quote["minute_start"]) + timedelta(minutes=1)),
        "open": quote["price"],
        "high": quote["price"],
        "low": quote["price"],
        "close": quote["price"],
        "volume": delta,
        "partial": partial,
        "first_sequence": quote["sequence"],
        "last_sequence": quote["sequence"],
        "close_received_at": quote["received_at"],
    }


def _halt(state: dict[str, Any], reason: str) -> dict[str, Any]:
    state.update(
        status="HALTED",
        reason=reason,
        quality="REJECTED",
        current_minute=None,
        completed_bar=None,
        pending_completed_bar=None,
    )
    return state


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
