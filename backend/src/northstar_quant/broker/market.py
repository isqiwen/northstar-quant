"""Decode explicit CTP SHFE daytime quotes and their source/receipt clocks."""

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.broker.events import BrokerEvent
from northstar_quant.market_data.sessions import SessionSchedule, resolve_trading_day

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = ((time(9), time(10, 15)), (time(10, 30), time(11, 30)), (time(13, 30), time(15)))
FRESH = timedelta(seconds=5)
_CLOCK = timedelta(seconds=1)


def decode_quote(
    event: BrokerEvent,
    instrument: str,
    tick: Decimal,
    now: datetime,
    schedule: SessionSchedule | None = None,
) -> dict[str, Any]:
    row = event.data or {}
    if str(row.get("InstrumentID", "")).upper() != instrument.upper():
        raise ValueError("INSTRUMENT_MISMATCH")
    if row.get("ExchangeID") not in (None, "", "SHFE"):
        raise ValueError("EXCHANGE_MISMATCH")
    local = ctp_quote_time(row, schedule=schedule).astimezone(SHANGHAI)
    day = row["TradingDay"]
    if schedule is not None and schedule.available_at > min(now, local):
        raise ValueError("SESSION_SCHEDULE_NOT_AVAILABLE")
    segment = (
        next((index for index, (start, end) in enumerate(DAY) if start <= local.time() < end), None)
        if schedule is None
        else next(
            (
                index
                for index, window in enumerate(schedule.windows)
                if window.opens_at <= local < window.closes_at
            ),
            None,
        )
    )
    if segment is None:
        raise ValueError("OUTSIDE_SHFE_DAY")
    at, received = local.astimezone(UTC), _at(event.received_at)
    if received - now > _CLOCK:
        raise ValueError("RECEIVE_TIME_IN_FUTURE")
    if at - received > _CLOCK:
        raise ValueError("SOURCE_TIME_IN_FUTURE")
    if now - at > FRESH or now - received > FRESH:
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
        "content_hash": _hash(dict(row)),
    }


def ctp_quote_time(row: Mapping[str, Any], *, schedule: SessionSchedule | None = None) -> datetime:
    """Resolve ActionDay as civil time and separately verify declared TradingDay."""
    day, action = row.get("TradingDay"), row.get("ActionDay")
    if any(
        not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None
        for value in (day, action)
    ) or (schedule is None and action != day):
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
        local = datetime.combine(
            date.fromisoformat(str(action)), time.fromisoformat(clock), SHANGHAI
        )
    except ValueError as error:
        raise ValueError("SOURCE_TIME_NOT_CONFIRMED") from error
    local += timedelta(milliseconds=millis)
    if schedule is not None and schedule.available_at > local:
        raise ValueError("SESSION_SCHEDULE_NOT_AVAILABLE")
    if schedule is not None and resolve_trading_day(local, schedule.windows) != date.fromisoformat(
        str(day)
    ):
        raise ValueError("SOURCE_SESSION_NOT_CONFIRMED")
    return local.astimezone(UTC)


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
