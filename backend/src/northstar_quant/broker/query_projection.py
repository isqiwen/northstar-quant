"""Derive query completeness and uncertainty from copied native receipts.

The same pure transformation is used for initial recording and cold recovery.
It neither reads storage nor establishes an account snapshot or send authority.
"""

from __future__ import annotations

import re
from typing import cast

from .events import ACCOUNT_ACTIVITY_CALLBACKS, TRANSFER_CALLBACKS, QueryCapture

# One ordered native query plan is used by reception and evidence reconstruction.
QUERY_TYPES = (
    ("account", "TradingAccount"),
    ("positions", "InvestorPosition"),
    ("orders", "Order"),
    ("trades", "Trade"),
    ("instrument", "Instrument"),
    ("margin", "InstrumentMarginRate"),
    ("commission", "InstrumentCommissionRate"),
)
_QUERIES = {section: ("ReqQry" + suffix, "OnRspQry" + suffix) for section, suffix in QUERY_TYPES}
_ACCOUNT_ROWS = {"account", "positions", "orders", "trades"}


def project_query(binding: dict[str, object], capture: QueryCapture | None) -> dict[str, object]:
    sections: dict[str, dict[str, object]] = {
        name: {
            "status": "NOT_OBSERVED",
            "request_id": None,
            "rows": None,
            "first_received_at": None,
            "last_received_at": None,
            "error_ids": [],
        }
        for name in _QUERIES
    }
    sections["instrument"]["identity"] = "UNKNOWN"
    reasons: set[str] = set()
    unknown: set[str] = {
        "TRADING_SESSION_TIMES_NOT_VERIFIED",
        "LOCAL_LEDGER_NOT_ESTABLISHED",
        "QUERY_WINDOW_IS_NOT_AN_ATOMIC_ACCOUNT_SNAPSHOT",
    }
    # This is the TD account identity. MD may omit account fields entirely;
    # observing that public market session cannot undo verified account facts.
    identity = "UNKNOWN"
    trading_day: str | None = None
    market: dict[str, object] = {
        "status": "NOT_OBSERVED",
        "login": None,
        "login_identity": "UNKNOWN",
        "depth": None,
        "continuous_feed": False,
    }
    fatal = False
    if capture is not None:
        requests: dict[tuple[str, int], tuple[str, str, int]] = {}
        terminated: set[tuple[str, int]] = set()
        profile = cast(dict[str, object], binding["profile"])
        account_id, instrument = binding["account_id"], binding["instrument"]
        td_connected = False
        context_seen = False
        for event in capture.events:
            data = event.data
            key = None if event.request_id is None else (event.channel, event.request_id)
            if event.error_id:
                reasons.add("BROKER_REPORTED_ERROR")
                fatal = True
            if event.callback == "CaptureStarted":
                expected_context = {
                    "profile_name": profile["name"],
                    "td_front": profile["td_front"],
                    "md_front": profile["md_front"],
                    "broker_id": profile["broker_id"],
                    "account_id": account_id,
                    "instrument": instrument,
                }
                if (
                    context_seen
                    or event.sequence != 1
                    or event.channel != "TD"
                    or data != expected_context
                ):
                    reasons.add("CAPTURE_ENVIRONMENT_BINDING_MISMATCH")
                    fatal = True
                else:
                    context_seen = True
            if event.callback == "OnFrontConnected" and event.channel == "TD":
                td_connected = True
            if event.callback == "OnFrontDisconnected":
                reasons.add(f"{event.channel}_DISCONNECTED_DURING_CAPTURE")
                fatal = True
            if event.callback == "OnHeartBeatWarning":
                reasons.add(f"{event.channel}_HEARTBEAT_WARNING")
            if event.callback == "RequestSent":
                if data is None or type(data.get("return_code")) is not int:
                    reasons.add("REQUEST_EVIDENCE_MISSING")
                    continue
                section, method = data.get("section"), data.get("method")
                if event.channel == "MD" and section == "depth" and method == "SubscribeMarketData":
                    market["subscription_requested_at"] = event.received_at
                    market["subscription_return_code"] = data["return_code"]
                    if data["return_code"] != 0:
                        reasons.add("SDK_REJECTED_MARKET_SUBSCRIPTION")
                        fatal = True
                    continue
                if key is None:
                    reasons.add("REQUEST_EVIDENCE_MISSING")
                    continue
                if key in requests:
                    reasons.add("REQUEST_ID_REUSED_WITHIN_CAPTURE")
                    continue
                requests[key] = (str(section), str(method), cast(int, data["return_code"]))
                if data["return_code"] != 0:
                    reasons.add("SDK_REJECTED_REQUEST")
                    fatal = True
                if event.channel == "TD" and section in _QUERIES:
                    if identity != "CONFIRMED":
                        reasons.add("QUERY_SENT_BEFORE_CONFIRMED_LOGIN")
                    item = sections[str(section)]
                    if item["status"] != "NOT_OBSERVED":
                        reasons.add("QUERY_SECTION_REQUESTED_MORE_THAN_ONCE")
                    if method != _QUERIES[str(section)][0]:
                        reasons.add("QUERY_METHOD_SCOPE_MISMATCH")
                    item.update(
                        status="WAITING" if data["return_code"] == 0 else "ERROR",
                        request_id=event.request_id,
                        rows=[],
                    )
                continue
            if event.callback == "OnRspUserLogin":
                request = None if key is None else requests.get(key)
                login_complete = (
                    request == ("login", "ReqUserLogin", 0)
                    and key not in terminated
                    and event.is_last is True
                )
                if not login_complete:
                    reasons.add("LOGIN_REQUEST_OR_COMPLETION_NOT_CONFIRMED")
                if key is not None and event.is_last is True:
                    terminated.add(key)
                if data is not None and not event.error_id:
                    matching = (
                        data.get("BrokerID") == profile["broker_id"]
                        and data.get("UserID") == account_id
                    )
                    if event.channel == "MD":
                        market["login"] = dict(data)
                        market["status"] = "LOGIN_OBSERVED"
                        if trading_day is None or data.get("TradingDay") != trading_day:
                            reasons.add("MARKET_LOGIN_TRADING_DAY_MISMATCH")
                            fatal = True
                        if any(
                            data.get(field) not in {None, "", expected}
                            for field, expected in (
                                ("BrokerID", profile["broker_id"]),
                                ("UserID", account_id),
                            )
                        ):
                            market["login_identity"] = "MISMATCH"
                            reasons.add("MARKET_LOGIN_IDENTITY_MISMATCH")
                            fatal = True
                        elif matching and login_complete and market["login_identity"] != "MISMATCH":
                            market["login_identity"] = "CONFIRMED"
                        else:
                            unknown.add("MARKET_LOGIN_IDENTITY_UNKNOWN")
                    elif not matching:
                        identity = "MISMATCH"
                        reasons.add("LOGIN_ACCOUNT_IDENTITY_MISMATCH")
                        fatal = True
                    else:
                        if login_complete and identity != "MISMATCH":
                            identity = "CONFIRMED"
                        day = data.get("TradingDay")
                        if isinstance(day, str) and re.fullmatch(r"[0-9]{8}", day):
                            trading_day = day
                        else:
                            reasons.add("BROKER_TRADING_DAY_UNKNOWN")
                else:
                    reasons.add("LOGIN_RESPONSE_MISSING_OR_FAILED")
            if event.callback == "OnRspAuthenticate":
                request = None if key is None else requests.get(key)
                if (
                    event.channel != "TD"
                    or request != ("authenticate", "ReqAuthenticate", 0)
                    or key in terminated
                    or event.is_last is not True
                ):
                    reasons.add("AUTHENTICATION_REQUEST_OR_COMPLETION_NOT_CONFIRMED")
                if key is not None and event.is_last is True:
                    terminated.add(key)
                if data is None or event.error_id:
                    reasons.add("AUTHENTICATION_RESPONSE_MISSING_OR_FAILED")
                elif (
                    data.get("BrokerID") != profile["broker_id"] or data.get("UserID") != account_id
                ):
                    reasons.add("AUTHENTICATION_ACCOUNT_IDENTITY_MISMATCH")
                    identity = "MISMATCH"
                    fatal = True
            section = next(
                (name for name, (_, callback) in _QUERIES.items() if callback == event.callback),
                None,
            )
            if section is not None:
                item = sections[section]
                request = None if key is None else requests.get(key)
                if (
                    event.channel != "TD"
                    or request is None
                    or request[0] != section
                    or key in terminated
                ):
                    reasons.add("UNMATCHED_OR_LATE_QUERY_RESPONSE")
                    continue
                if item["request_id"] != event.request_id:
                    reasons.add("QUERY_RESPONSE_REQUEST_ID_MISMATCH")
                    continue
                if item["first_received_at"] is None:
                    item["first_received_at"] = event.received_at
                item["last_received_at"] = event.received_at
                if event.error_id:
                    cast(list[int], item["error_ids"]).append(event.error_id)
                    item["status"] = "ERROR"
                # CTP's InstrumentID query can return prefix matches, including
                # options. Preserve every callback in capture, while this
                # selected-contract projection accepts exact equality only.
                if data is not None and (
                    section != "instrument" or data.get("InstrumentID") == instrument
                ):
                    cast(list[dict[str, object]], item["rows"]).append(dict(data))
                    if section in _ACCOUNT_ROWS:
                        investor_key = "AccountID" if section == "account" else "InvestorID"
                        if (
                            data.get("BrokerID") != profile["broker_id"]
                            or data.get(investor_key) != account_id
                        ):
                            reasons.add("QUERY_ACCOUNT_IDENTITY_MISMATCH")
                            identity = "MISMATCH"
                            fatal = True
                        if data.get("TradingDay") != trading_day or trading_day is None:
                            reasons.add("QUERY_TRADING_DAY_UNCONFIRMED")
                        if section == "account" and data.get("CurrencyID") != "CNY":
                            reasons.add("ACCOUNT_CURRENCY_MISMATCH")
                            fatal = True
                    elif data.get("InstrumentID") != instrument:
                        reasons.add("INSTRUMENT_QUERY_IDENTITY_MISMATCH")
                        fatal = True
                    if section in {"margin", "commission"}:
                        if data.get("BrokerID") not in {None, "", profile["broker_id"]} or data.get(
                            "InvestorID"
                        ) not in {None, "", account_id}:
                            reasons.add("TERMS_ACCOUNT_IDENTITY_MISMATCH")
                            fatal = True
                if event.is_last is True:
                    assert key is not None
                    terminated.add(key)
                    if item["status"] != "ERROR":
                        item["status"] = "COMPLETE"
                elif event.is_last is None:
                    reasons.add("QUERY_COMPLETION_FLAG_UNKNOWN")
            if event.callback in ACCOUNT_ACTIVITY_CALLBACKS:
                unknown.add("ACCOUNT_EVENTS_ARRIVED_DURING_QUERY")
                if data is not None and (
                    data.get("BrokerID") != profile["broker_id"]
                    or data.get(
                        "AccountID" if event.callback in TRANSFER_CALLBACKS else "InvestorID"
                    )
                    != account_id
                ):
                    reasons.add("ACCOUNT_CALLBACK_IDENTITY_MISMATCH")
                    fatal = True
            if event.callback == "OnRtnDepthMarketData":
                if event.channel != "MD" or data is None or data.get("InstrumentID") != instrument:
                    reasons.add("MARKET_SNAPSHOT_IDENTITY_MISMATCH")
                else:
                    market.update(
                        status="SNAPSHOT_OBSERVED",
                        depth={
                            "received_at": event.received_at,
                            "data": dict(data),
                        },
                    )
            if event.callback == "OnRspSubMarketData":
                if (
                    event.channel != "MD"
                    or data is None
                    or data.get("InstrumentID") != instrument
                    or "subscription_requested_at" not in market
                ):
                    reasons.add("MARKET_SUBSCRIPTION_CONTEXT_NOT_CONFIRMED")
                else:
                    market["subscription"] = {
                        "received_at": event.received_at,
                        "request_id": event.request_id,
                        "is_last": event.is_last,
                        "error_id": event.error_id,
                        "data": dict(data),
                    }
        for request_key, (request_section, _, _) in requests.items():
            if request_section in {"authenticate", "login"} and request_key not in terminated:
                reasons.add("IDENTITY_REQUEST_NOT_COMPLETED")
        if not td_connected:
            reasons.add("TD_CONNECTION_NOT_OBSERVED")
        if not context_seen:
            reasons.add("LOCAL_CONNECTION_CONTEXT_NOT_CONFIRMED")
        if identity != "CONFIRMED":
            reasons.add("BROKER_ACCOUNT_IDENTITY_NOT_CONFIRMED")
        if trading_day is None:
            reasons.add("BROKER_TRADING_DAY_UNKNOWN")
        for name, section in sections.items():
            if section["status"] != "COMPLETE":
                reasons.add(f"{name.upper()}_QUERY_NOT_COMPLETE")
            if name == "instrument" and section["status"] == "COMPLETE":
                count = len(cast(list[dict[str, object]], section["rows"]))
                if count == 1:
                    section["identity"] = "CONFIRMED"
                else:
                    reasons.add(
                        "EXACT_INSTRUMENT_NOT_FOUND"
                        if count == 0
                        else "EXACT_INSTRUMENT_NOT_UNIQUE"
                    )
            if name in {"account", "instrument", "margin", "commission"} and not section["rows"]:
                unknown.add(f"{name.upper()}_FACTS_NOT_RETURNED")
        if capture.failure_code is not None:
            reasons.add(capture.failure_code)
            fatal = True
        if capture.trader_api_version is None:
            unknown.add("TRADER_API_VERSION_UNKNOWN")
        if market["login_identity"] == "UNKNOWN":
            unknown.add("MARKET_LOGIN_IDENTITY_UNKNOWN")
    else:
        reasons.add("QUERY_NOT_FINISHED_OR_CALLER_INTERRUPTED")
    status = (
        "PENDING"
        if capture is None
        else "FAILED"
        if fatal
        else "INCOMPLETE"
        if reasons
        else "COMPLETE"
    )
    return {
        "status": status,
        "capture": None if capture is None else capture.to_dict(),
        "completeness": {
            "status": "COMPLETE" if status == "COMPLETE" else "INCOMPLETE",
            "identity": identity,
            "trading_day": trading_day,
            "sections": sections,
            "reasons": sorted(reasons),
        },
        "market": market,
        "reconciliation": {
            "status": "UNRECONCILED",
            "local_ledger": "NOT_ESTABLISHED",
            "differences": None,
            "reasons": sorted(unknown | reasons),
        },
        "execution": {"order_sending": False, "cancel_sending": False},
    }
