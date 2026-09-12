"""Map retained CTP account/trade observations to exact, identified values.

No database writes, account reconstruction, eligibility decisions or execution authority.
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.observations import ACCOUNT_AMOUNT_FIELDS, compare_account_amounts
from northstar_quant.broker.events import ACCOUNT_ACTIVITY_CALLBACKS, TRANSFER_CALLBACKS

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

_ACTIVITY = ("positions", "orders", "trades")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


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


def decode_trade(row: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
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


def query_trades(batch: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
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


def stream_trades(
    prefix: dict[str, Any], after_sequence: int, day: str
) -> tuple[list[tuple[int, dict[str, Any]]], list[dict[str, Any]]]:
    """Keep source problems separate from observed trades; never turn a stream into a query."""
    binding = prefix["binding"]
    observed, problems = [], []
    confirmed_day = None
    last_received = None
    for item in prefix["events"]:
        event, row = item["event"], item["event"]["data"] or {}
        locator = {"source_stream_id": prefix["stream_id"], "sequence": event["sequence"]}
        if event["channel"] == "TD" and event["callback"] == "OnRspUserLogin":
            if (
                not event["error_id"]
                and row.get("UserID") == binding["account_id"]
                and row.get("BrokerID") == binding["profile"]["broker_id"]
                and row.get("TradingDay") == day
            ):
                confirmed_day = day
            else:
                confirmed_day = None
                problems.append({"code": "STREAM_TD_IDENTITY_NOT_CONFIRMED", **locator})
        if event["error_id"] or event["callback"] in {"OnFrontDisconnected", "OnHeartBeatWarning"}:
            confirmed_day = None
            problems.append({"code": "STREAM_ACCOUNT_CONNECTION_ERROR", **locator})
        received = _time(event["received_at"])
        if last_received is not None and received < last_received:
            problems.append({"code": "STREAM_ACCOUNT_RECEIPT_TIME_REGRESSED", **locator})
        last_received = received
        if event["sequence"] <= after_sequence:
            continue
        if event["callback"] in {"OnRtnTrade", "OnRtnOrder"} and (
            event["channel"] != "TD"
            or row.get("BrokerID") != binding["profile"]["broker_id"]
            or row.get("InvestorID") != binding["account_id"]
            or row.get("TradingDay") != day
            or confirmed_day != day
        ):
            problems.append({"code": "STREAM_ACCOUNT_CALLBACK_IDENTITY_NOT_CONFIRMED", **locator})
        if event["callback"] in TRANSFER_CALLBACKS:
            problems.append({"code": "STREAM_CASHFLOW_RECONCILIATION_REQUIRED", **locator})
        if event["callback"] == "OnRtnTrade":
            if event["error_id"] or event["channel"] != "TD" or event["data"] is None:
                problems.append({"code": "STREAM_TRADE_CALLBACK_NOT_CONFIRMED", **locator})
            else:
                observed.append((event["sequence"], row))
    if confirmed_day is None:
        problems.append(
            {"code": "STREAM_TD_IDENTITY_NOT_CONFIRMED", "source_stream_id": prefix["stream_id"]}
        )
    return observed, problems


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


def account_baseline(batch: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any], list[str]]:
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
    elif any(event["callback"] in ACCOUNT_ACTIVITY_CALLBACKS for event in capture["events"]):
        reasons.append("ACCOUNT_ACTIVITY_DURING_QUERY")
    return funds, activity, sorted(set(reasons))


def account_observation(batch: dict[str, Any]) -> dict[str, Any]:
    section = batch["completeness"]["sections"]["account"]
    capture = batch["capture"]
    problems = []
    if batch["status"] != "COMPLETE":
        problems.append("QUERY_NOT_COMPLETE")
    if batch["completeness"]["identity"] != "CONFIRMED":
        problems.append("TD_ACCOUNT_IDENTITY_NOT_CONFIRMED")
    rows = section["rows"]
    row = rows[0] if section["status"] == "COMPLETE" and rows and len(rows) == 1 else None
    if row is None:
        problems.append("ONE_COMPLETE_CNY_ACCOUNT_REQUIRED")
    elif (
        row.get("BrokerID") != batch["profile"]["broker_id"]
        or row.get("AccountID") != batch["account_id"]
        or row.get("CurrencyID") != "CNY"
        or row.get("BizType") != "1"
        or row.get("TradingDay") != batch["completeness"]["trading_day"]
        or type(row.get("SettlementID")) is not int
    ):
        problems.append("ACCOUNT_SCOPE_NOT_CONFIRMED")
    callbacks = (
        []
        if capture is None
        else [
            {"sequence": event["sequence"], "received_at": event["received_at"]}
            for event in capture["events"]
            if event["channel"] == "TD"
            and event["callback"] == "OnRspQryTradingAccount"
            and event["request_id"] == section["request_id"]
            and event["data"] is not None
            and event["error_id"] == 0
        ]
    )
    if len(callbacks) != 1:
        problems.append("ACCOUNT_RECEIPT_NOT_UNIQUE")
    scope_confirmed = not problems
    amounts = (
        {} if row is None else {name: row[name] for name in ACCOUNT_AMOUNT_FIELDS if name in row}
    )
    # The arithmetic owner checks finite exact values, including negative actual balances.
    checked = compare_account_amounts(amounts, amounts, same_scope=True)
    problems.extend(
        code for code in cast(list[str], checked["problems"]) if not code.endswith("_PREVIOUS")
    )
    return {
        "source_batch_id": batch["batch_id"],
        "query_started_at": None if capture is None else capture["started_at"],
        "query_finished_at": None if capture is None else capture["finished_at"],
        "account_receipts": callbacks,
        "scope": None
        if row is None
        else {
            name: row.get(name)
            for name in (
                "BrokerID",
                "AccountID",
                "CurrencyID",
                "BizType",
                "TradingDay",
                "SettlementID",
            )
        },
        "amounts": amounts,
        "scope_confirmed": scope_confirmed,
        "problems": sorted(set(problems)),
        "account_activity_during_query": capture is not None
        and any(event["callback"] in ACCOUNT_ACTIVITY_CALLBACKS for event in capture["events"]),
    }


def position_observations(
    batch: dict[str, Any],
) -> tuple[dict[tuple[str, str, str, str], dict[str, int]], bool, list[dict[str, Any]]]:
    """Decode gross SHFE today/yesterday quantities, retaining unknown scope."""
    problems: list[dict[str, Any]] = []
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
    return observed, complete, problems
