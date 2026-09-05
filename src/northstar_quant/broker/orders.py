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

from northstar_quant.strategy import decimal_text

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


def _key(exchange: str, identifier: str, batch: dict[str, Any]) -> str:
    scope = [batch["profile"], batch["account_id"], batch["completeness"]["trading_day"]]
    return hashlib.sha256(
        json.dumps([*scope, exchange, identifier], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _order(row: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    if (row.get("BrokerID"), row.get("InvestorID"), row.get("TradingDay")) != (
        batch["profile"]["broker_id"],
        batch["account_id"],
        batch["completeness"]["trading_day"],
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
    instruments = batch["completeness"]["sections"]["instrument"]
    if (
        instruments["status"] != "COMPLETE"
        or len(instruments["rows"]) != 1
        or instruments["rows"][0].get("ProductClass") != "1"
        or instruments["rows"][0].get("ExchangeID") != exchange
        or instruments["rows"][0].get("InstrumentID") != row.get("InstrumentID")
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
        "order_id": _key(exchange, order_sys_id, batch),
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


def _terms(order: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in order.items()
        if key
        not in {
            "reported_traded_lots",
            "reported_remaining_lots",
            "order_state",
            "submit_state",
            "active",
            "problems",
            "client_identity",
        }
    }


def _observations(batch: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
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


def inspect_orders(
    check: dict[str, Any], batches: list[dict[str, Any]], fills: list[dict[str, Any]]
) -> dict[str, Any]:
    """Use fixed position-check inputs; never import its new fills or change its result."""
    latest = batches[-1]
    problems = list(check["problems"])
    observations: dict[str, list[dict[str, Any]]] = {}
    previous: dict[str, dict[str, Any]] = {}
    latest_orders: dict[str, dict[str, Any]] = {}
    queried_latest: set[str] = set()
    aliases: dict[tuple[object, ...], str] = {}
    unresolved = []
    count = 0
    for batch in batches:
        current: dict[str, dict[str, Any]] = {}
        for locator, raw in _observations(batch):
            count += 1
            if count > 10000:
                raise ValueError("order review exceeds 10000 bounded observations")
            try:
                order = _order(raw, batch)
            except ValueError:
                unresolved.append({**locator, "reported_fields": raw})
                problems.append({"code": "ORDER_FIELDS_NOT_CONFIRMED", **locator})
                continue
            key = order["order_id"]
            if batch is latest and locator["callback"] == "OnRspQryOrder":
                queried_latest.add(key)
            observations.setdefault(key, []).append(
                {
                    **locator,
                    "order_state": order["order_state"],
                    "submit_state": order["submit_state"],
                    "reported_traded_lots": order["reported_traded_lots"],
                    "reported_remaining_lots": order["reported_remaining_lots"],
                }
            )
            for problem in order["problems"]:
                problems.append({**problem, "order_id": key, **locator})
            earlier = current.get(key)
            if earlier is not None and earlier != order:
                problems.append(
                    {"code": "ORDER_OBSERVATIONS_AMBIGUOUS", "order_id": key, **locator}
                )
                # Keep the first observation; neither arrival order nor maxima resolve ambiguity.
                continue
            client = order["client_identity"]
            if client is not None:
                alias = tuple(client)
                if alias in aliases and aliases[alias] != key:
                    problems.append(
                        {"code": "CLIENT_ORDER_IDENTITY_CONFLICT", "order_id": key, **locator}
                    )
                else:
                    aliases[alias] = key
            prior = previous.get(key)
            if prior is not None:
                if _terms(prior) != _terms(order):
                    problems.append({"code": "ORDER_IDENTITY_CONFLICT", "order_id": key, **locator})
                if (
                    prior["client_identity"] is not None
                    and client is not None
                    and prior["client_identity"] != client
                ):
                    problems.append(
                        {"code": "CLIENT_ORDER_IDENTITY_CHANGED", "order_id": key, **locator}
                    )
                if prior["reported_traded_lots"] > order["reported_traded_lots"]:
                    problems.append(
                        {"code": "ORDER_CUMULATIVE_VOLUME_REGRESSED", "order_id": key, **locator}
                    )
                if prior["active"] is False and (
                    order["active"] is not False
                    or (prior["order_state"], prior["reported_traded_lots"])
                    != (order["order_state"], order["reported_traded_lots"])
                ):
                    problems.append(
                        {"code": "ORDER_TERMINAL_STATE_CHANGED", "order_id": key, **locator}
                    )
            current[key] = order
        for key in sorted(set(previous) - set(current)):
            problems.append(
                {
                    "code": "PREVIOUS_ORDER_MISSING_FROM_QUERY",
                    "order_id": key,
                    "source_batch_id": batch["batch_id"],
                }
            )
        if batch is latest:
            latest_orders = current
        previous.update(current)
    missing = set(latest_orders) - queried_latest
    for key in sorted(missing):
        problems.append({"code": "PREVIOUS_ORDER_MISSING_FROM_QUERY", "order_id": key})

    linked: dict[str, list[dict[str, Any]]] = {}
    unlinked = []
    for fill in fills:
        key = _key(fill["exchange"], fill["order_sys_id"], latest)
        matched_order = previous.get(key)
        if matched_order is None or (
            fill["symbol"],
            fill["direction"],
            fill["offset_flag"],
            fill["hedge_flag"],
        ) != (
            matched_order["symbol"],
            matched_order["direction"],
            matched_order["offset_flag"],
            matched_order["hedge_flag"],
        ):
            unlinked.append(fill)
            if matched_order is not None:
                problems.append(
                    {
                        "code": "ORDER_FILL_TERMS_CONFLICT",
                        "order_id": key,
                        "fill_id": fill["fill_id"],
                    }
                )
            else:
                problems.append({"code": "RECORDED_FILL_WITHOUT_ORDER", "fill_id": fill["fill_id"]})
            continue
        if fill["trading_day"] != latest["completeness"]["trading_day"]:
            unlinked.append(fill)
            problems.append({"code": "ORDER_FILL_TRADING_DAY_CONFLICT", "fill_id": fill["fill_id"]})
            continue
        linked.setdefault(key, []).append(fill)
    result = []
    for key, order in sorted(previous.items()):
        matched = linked.get(key, [])
        recorded = sum(fill["quantity_lots"] for fill in matched)
        row_problems = [problem for problem in problems if problem.get("order_id") == key]
        seen = key in queried_latest
        result.append(
            {
                **order,
                "contract_id": next(
                    (fill["contract_id"] for fill in matched if fill["contract_id"] is not None),
                    None,
                ),
                "ledger_filled_lots": recorded,
                "fill_gap_lots": order["reported_traded_lots"] - recorded,
                "ledger_fill_ids": [fill["fill_id"] for fill in matched],
                "unrecorded_fill_ids": [
                    fill["fill_id"]
                    for fill in check["unrecorded_fills"]
                    if _key(fill["exchange"], fill["order_sys_id"], latest) == key
                ],
                "seen_in_query": seen,
                "active": order["active"] if seen and not problems else None,
                "observations": observations[key],
                "problems": row_problems,
                "ownership": "EXTERNAL_NOT_OWNED",
                "reservation_release": "NOT_AUTHORIZED",
            }
        )
    changed = unlinked or check["unrecorded_fills"] or any(row["fill_gap_lots"] for row in result)
    return {
        "status": "UNKNOWN" if problems else "DIFFERENCES" if changed else "MATCHED",
        "orders": result,
        "unlinked_fills": unlinked,
        "unresolved_observations": unresolved,
        "unrecorded_fills": check["unrecorded_fills"],
        "problems": problems,
    }
