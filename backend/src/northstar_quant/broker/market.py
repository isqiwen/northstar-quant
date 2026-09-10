"""Decode explicit CTP SHFE daytime quotes and their source/receipt clocks."""

import hashlib
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.broker.events import BrokerEvent

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = ((time(9), time(10, 15)), (time(10, 30), time(11, 30)), (time(13, 30), time(15)))
FRESH = timedelta(seconds=5)
_CLOCK = timedelta(seconds=1)


def decode_quote(
    event: BrokerEvent, instrument: str, tick: Decimal, now: datetime
) -> dict[str, Any]:
    row = event.data or {}
    if str(row.get("InstrumentID", "")).upper() != instrument.upper():
        raise ValueError("INSTRUMENT_MISMATCH")
    if row.get("ExchangeID") not in (None, "", "SHFE"):
        raise ValueError("EXCHANGE_MISMATCH")
    local = ctp_day_quote_time(row).astimezone(SHANGHAI)
    day = row["TradingDay"]
    segment = next(
        (index for index, (start, end) in enumerate(DAY) if start <= local.time() < end), None
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
        "content_hash": _hash(row),
    }


def ctp_day_quote_time(row: dict[str, Any]) -> datetime:
    """Resolve the source clock used by DAY sampling and opening-budget freshness.

    Receipt or calculation time never substitutes for a missing CTP source clock.
    This only resolves explicit same-day timestamps, not a night-session calendar.
    """
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
        local = datetime.combine(date.fromisoformat(day), time.fromisoformat(clock), SHANGHAI)
    except ValueError as error:
        raise ValueError("SOURCE_TIME_NOT_CONFIRMED") from error
    local += timedelta(milliseconds=millis)
    return local.astimezone(UTC)


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
