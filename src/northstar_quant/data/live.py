"""Observe SHFE DAY snapshots and emit account-neutral, completed-minute signals.

This is not a trade-tape reconstruction or an exchange-published OHLCV product.
LastPrice samples form observed OHLC; cumulative-volume differences are assigned
to the timestamp of their arriving snapshot. No missing minute is synthesized.
The caller persists each BrokerEvent and the returned checkpoint atomically, and
owns connection/subscription identity, absence-of-input freshness and activation.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from northstar_quant.broker.records import BrokerEvent
from northstar_quant.research import ResearchConfig
from northstar_quant.strategy import decimal_text, momentum_intent

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DAY = ((time(9), time(10, 15)), (time(10, 30), time(11, 30)), (time(13, 30), time(15)))
_FRESH = timedelta(seconds=5)
_CLOCK = timedelta(seconds=1)


def advance_market(
    state: dict[str, Any],
    event: BrokerEvent,
    *,
    instrument: str,
    contract_id: UUID,
    price_tick: Decimal,
    config: ResearchConfig,
    now: datetime,
) -> dict[str, Any]:
    """Advance one persisted event without mutating state or performing any I/O.

    Only a caller-verified, fixed SHFE futures contract is supported. All source
    dates must be explicit; sessions cannot cross a TradingDay. Source/receipt
    clocks may lead their observer by at most one second; source and receipt age
    and intra-session snapshot gaps may not exceed five seconds. These are fixed
    engineering limits, not exchange guarantees. Unknown ordering stops shadow
    calculation and clears warmup; only an explicitly new empty state can reset it.

    completed_bar/intent contain only this event's new result, never an earlier
    result repeated as new. recent_closes retains at most lookback+1 summaries.
    One pending_completed_bar handles clock tolerance without early completion.
    Initial and post-break minutes are partial; terminal minutes are never flushed.
    Configuration binding covers Strategy only; no Risk or simulated account runs.
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
        "strategy": {
            "lookback": config.lookback,
            "threshold": decimal_text(config.threshold),
            "target_fraction": decimal_text(config.target_fraction),
            "lifetime_seconds": config.order_lifetime_seconds,
        },
    }
    if state and state.get("binding") != binding:
        raise ValueError("live market checkpoint cannot change its fixed binding")
    result = (
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
            "recent_closes": [],
            "pending_completed_bar": None,
        }
    )
    result["completed_bar"] = result["intent"] = None
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
        quote = _quote(event, instrument, price_tick, now)
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
        if quote["segment"] == previous["segment"] and elapsed > _FRESH:
            return _halt(result, "SOURCE_GAP")
        if quote["segment"] != previous["segment"]:
            day = date.fromisoformat(quote["trading_day"])
            previous_end = datetime.combine(day, _DAY[previous["segment"]][1], _SHANGHAI)
            next_start = datetime.combine(day, _DAY[quote["segment"]][0], _SHANGHAI)
            if (
                quote["segment"] != previous["segment"] + 1
                or previous_end - _at(previous["event_time"]) > _FRESH
                or _at(quote["event_time"]) - next_start > _FRESH
            ):
                return _halt(result, "SOURCE_GAP")
    result.update(trading_day=quote["trading_day"], last_quote=quote, quality="ACCEPTED")
    current = result["current_minute"]
    if previous is None or current is None or previous["segment"] != quote["segment"]:
        result.update(
            current_minute=_minute(quote, partial=True, delta=None),
            recent_closes=[],
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
        recent = result["recent_closes"]
        recent.append(
            {
                key: pending[key]
                for key in (
                    "observation_id",
                    "close",
                    "first_sequence",
                    "last_sequence",
                    "confirmed_by_sequence",
                    "close_received_at",
                )
            }
        )
        result["recent_closes"] = recent = recent[-(config.lookback + 1) :]
        if len(recent) == config.lookback + 1:
            intent = momentum_intent(
                observation_id=UUID(pending["observation_id"]),
                contract_id=contract_id,
                at=now,
                previous_close=Decimal(recent[0]["close"]),
                close=Decimal(recent[-1]["close"]),
                threshold=config.threshold,
                target_fraction=config.target_fraction,
                lifetime_seconds=config.order_lifetime_seconds,
            )
            result["intent"] = {
                "intent_id": intent.intent_id,
                "observation_id": str(intent.observation_id),
                "contract_id": str(intent.contract_id),
                "generated_at": _iso(intent.generated_at),
                "valid_until": _iso(intent.valid_until),
                "momentum": decimal_text(intent.momentum),
                "target_fraction": decimal_text(intent.target_fraction),
                "used_bars": deepcopy(recent),
            }
    ready = len(result["recent_closes"]) == config.lookback + 1
    result.update(
        status="READY" if ready else "WARMING_UP",
        reason="CLOCK_TOLERANCE_WAIT"
        if result["pending_completed_bar"] is not None
        else "SHADOW_INTENT"
        if result["intent"] is not None
        else "OBSERVING_MINUTE"
        if ready
        else "WARMING_COMPLETED_MINUTES",
    )
    return result


def _quote(event: BrokerEvent, instrument: str, tick: Decimal, now: datetime) -> dict[str, Any]:
    row = event.data or {}
    if str(row.get("InstrumentID", "")).upper() != instrument.upper():
        raise ValueError("INSTRUMENT_MISMATCH")
    if row.get("ExchangeID") not in (None, "", "SHFE"):
        raise ValueError("EXCHANGE_MISMATCH")
    day, action = row.get("TradingDay"), row.get("ActionDay")
    if not isinstance(day, str) or re.fullmatch(r"[0-9]{8}", day) is None or action != day:
        raise ValueError("SOURCE_DATES_NOT_CONFIRMED")
    clock, millis = row.get("UpdateTime"), row.get("UpdateMillisec")
    if (
        not isinstance(clock, str)
        or re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}", clock) is None
        or type(millis) is not int
        or not 0 <= millis < 1000
    ):
        raise ValueError("SOURCE_TIME_NOT_CONFIRMED")
    try:
        local = datetime.combine(date.fromisoformat(day), time.fromisoformat(clock), _SHANGHAI)
    except ValueError as error:
        raise ValueError("SOURCE_TIME_NOT_CONFIRMED") from error
    local += timedelta(milliseconds=millis)
    segment = next(
        (index for index, (start, end) in enumerate(_DAY) if start <= local.time() < end), None
    )
    if segment is None:
        raise ValueError("OUTSIDE_SHFE_DAY")
    at, received = local.astimezone(UTC), _at(event.received_at)
    if received - now > _CLOCK:
        raise ValueError("RECEIVE_TIME_IN_FUTURE")
    if at - received > _CLOCK:
        raise ValueError("SOURCE_TIME_IN_FUTURE")
    if now - at > _FRESH or now - received > _FRESH:
        raise ValueError("STALE_QUOTE")
    volume, raw_price = row.get("Volume"), row.get("LastPrice")
    if type(volume) is not int or not 0 <= volume <= 2**31 - 1:
        raise ValueError("INVALID_CUMULATIVE_VOLUME")
    try:
        price = Decimal(raw_price) if isinstance(raw_price, str) else Decimal("NaN")
    except InvalidOperation as error:
        raise ValueError("INVALID_PRICE") from error
    if not price.is_finite() or not Decimal("1e-18") <= price < Decimal("1e18"):
        raise ValueError("INVALID_PRICE")
    with localcontext() as context:
        context.prec = 96
        if price % tick:
            raise ValueError("PRICE_OFF_TICK")
    return {
        "sequence": event.sequence,
        "received_at": _iso(received),
        "event_time": _iso(at),
        "trading_day": day,
        "minute_start": _iso(at.replace(second=0, microsecond=0)),
        "segment": segment,
        "price": decimal_text(price),
        "cumulative_volume": volume,
        "content_hash": _hash(row),
    }


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
        recent_closes=[],
        current_minute=None,
        completed_bar=None,
        pending_completed_bar=None,
        intent=None,
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


def idle_reason(state: dict[str, Any] | None, *, now: datetime) -> str | None:
    """Classify absent SHFE DAY input without changing its verified checkpoint.

    No accepted quote means no inferred TradingDay or session status. A planned
    break/end requires the previous segment's final five seconds to have been
    observed on that same explicitly reported TradingDay; earlier gaps stay stale.
    This does not clear an existing halt, complete a bar or authorize execution.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("live processing time must be explicit UTC")
    if not state or state.get("status") == "HALTED":
        return None
    quote, trading_day = state.get("last_quote"), state.get("trading_day")
    if (
        not isinstance(quote, dict)
        or not isinstance(trading_day, str)
        or quote.get("trading_day") != trading_day
    ):
        return None
    day = date.fromisoformat(trading_day)
    observed, received = _at(quote["event_time"]), _at(quote["received_at"])
    local_now = now.astimezone(_SHANGHAI)
    segment = quote["segment"]
    end = datetime.combine(day, _DAY[segment][1], _SHANGHAI)
    if local_now.date() == day and timedelta(0) < end - observed <= _FRESH:
        if segment == len(_DAY) - 1 and local_now >= end:
            return "DAY_SESSION_ENDED"
        if segment < len(_DAY) - 1:
            next_start = datetime.combine(day, _DAY[segment + 1][0], _SHANGHAI)
            if end <= local_now < next_start:
                return "SCHEDULED_BREAK"
    if now - observed > _FRESH or now - received > _FRESH:
        return "QUOTE_STALE"
    return None
