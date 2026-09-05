"""Private native implementation for one explicitly requested SimNow read.

CTP owns callback pointers only while the callback runs. Copy permitted scalar
fields immediately, then drain a bounded queue from the worker's Python thread.
Never block a CTP callback on a database transaction or a process pipe.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from typing import Any, cast

from northstar_quant.broker.records import CALLBACK_FIELDS, BrokerEvent
from northstar_quant.broker.settings import Credentials, SimnowProfile

_MAX_EVENTS = 10_000
_MAX_BYTES = 8 * 1024 * 1024
_QUERY_INTERVAL = 1.1
_TD_QUERIES = (
    ("account", "TradingAccount"),
    ("positions", "InvestorPosition"),
    ("orders", "Order"),
    ("trades", "Trade"),
    ("instrument", "Instrument"),
    ("margin", "InstrumentMarginRate"),
    ("commission", "InstrumentCommissionRate"),
)


def _copy_fields(callback: str, native: object | None) -> dict[str, object] | None:
    if native is None:
        return None
    copied: dict[str, object] = {}
    for name in CALLBACK_FIELDS[callback]:
        value = getattr(native, name, None)
        if isinstance(value, float):
            # CTP's unassigned double sentinel is close to DBL_MAX. Preserve
            # absence rather than turning it into an enormous market price.
            value = str(value) if math.isfinite(value) and abs(value) < 1e100 else None
        elif isinstance(value, bytes):
            value = value.decode("gbk", errors="strict")
        if isinstance(value, str):
            value = value.rstrip("\x00")
        if value is not None and not isinstance(value, (str, int, bool)):
            raise ValueError("unsupported native scalar")
        copied[name] = value
    return copied


class _Receiver:
    def __init__(self, connection: Connection, timeout: float) -> None:
        self.connection = connection
        self.deadline = time.monotonic() + timeout
        self.lock = threading.Lock()
        self.queue: deque[dict[str, object]] = deque()
        self.sequence = 0
        self.total_bytes = 0
        self.failure: str | None = None
        self.connected: set[str] = set()
        self.responses: dict[tuple[str, str, int], dict[str, object] | None] = {}
        self.quote_seen = False
        self.closed = False

    def event(
        self,
        channel: str,
        callback: str,
        data: dict[str, object] | None = None,
        request_id: int | None = None,
        is_last: bool | None = None,
        error_id: int = 0,
    ) -> dict[str, object] | None:
        with self.lock:
            if self.closed or self.failure == "CAPTURE_LIMIT_EXCEEDED":
                return None
            if callback == "OnRtnDepthMarketData" and self.quote_seen:
                return None
            if self.sequence >= _MAX_EVENTS:
                self.failure = "CAPTURE_LIMIT_EXCEEDED"
                return None
            event = BrokerEvent(
                sequence=self.sequence + 1,
                channel=channel,
                callback=callback,
                request_id=request_id,
                is_last=is_last,
                received_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                error_id=error_id,
                data=data,
            )
            event_data = event.to_dict()
            encoded = json.dumps({"kind": "event", "event": event_data}).encode()
            if self.total_bytes + len(encoded) > _MAX_BYTES:
                self.failure = "CAPTURE_LIMIT_EXCEEDED"
                return None
            self.total_bytes += len(encoded)
            self.sequence += 1
            self.queue.append(event_data)
            if callback == "OnFrontConnected":
                if channel in self.connected:
                    self.failure = self.failure or "RECONNECTED_DURING_CAPTURE"
                self.connected.add(channel)
            elif callback == "OnFrontDisconnected":
                self.failure = self.failure or "DISCONNECTED"
            elif error_id:
                self.failure = self.failure or "CTP_RESPONSE_ERROR"
            if is_last and request_id is not None:
                self.responses[(channel, callback, request_id)] = data
            if callback == "OnRtnDepthMarketData":
                self.quote_seen = True
            return event_data

    def callback(
        self,
        channel: str,
        callback: str,
        native: object | None,
        error: object | None = None,
        request_id: int | None = None,
        is_last: bool | None = None,
    ) -> None:
        try:
            self.event(
                channel,
                callback,
                _copy_fields(callback, native),
                request_id,
                is_last,
                int(getattr(error, "ErrorID", 0)),
            )
        except Exception:
            # Third-party native callbacks print uncaught exceptions (potentially
            # including account fields). Do not let an exception cross that seam.
            with self.lock:
                self.failure = self.failure or "CALLBACK_DECODE_FAILED"

    def drain(self) -> None:
        while True:
            with self.lock:
                if not self.queue:
                    return
                event = self.queue.popleft()
            self.connection.send_bytes(json.dumps({"kind": "event", "event": event}).encode())

    def wait(self, condition: Callable[[], bool], seconds: float | None = None) -> bool:
        deadline = min(self.deadline, time.monotonic() + seconds) if seconds else self.deadline
        while time.monotonic() < deadline:
            self.drain()
            if self.failure:
                return False
            if condition():
                return True
            time.sleep(0.02)
        self.drain()
        return False

    def request(
        self, api: Any, section: str, suffix: str, native: object, request_id: int, channel: str
    ) -> bool:
        method = "Req" + suffix
        callback = "OnRsp" + suffix
        pending = self.event(
            channel,
            "RequestSent",
            {"section": section, "method": method, "return_code": None},
            request_id,
        )
        if pending is None:
            return False
        code = int(getattr(api, method)(native, request_id))
        # Reserve order/time before the native call. Only this thread drains the
        # queue, after the actual immediate return code has replaced this value.
        cast(dict[str, object], pending["data"])["return_code"] = code
        if code:
            self.failure = self.failure or "CTP_REQUEST_REJECTED"
            self.drain()
            return False
        if not self.wait(lambda: (channel, callback, request_id) in self.responses):
            self.failure = self.failure or "QUERY_TIMEOUT"
            return False
        return True


def _response(receiver: _Receiver, channel: str, callback: str) -> Any:
    def respond(_self: object, native: object, error: object, request: int, last: bool) -> None:
        receiver.callback(channel, callback, native, error, request, last)

    return respond


def _notification(receiver: _Receiver, channel: str, callback: str) -> Any:
    def notify(_self: object, native: object) -> None:
        receiver.callback(channel, callback, native)

    return notify


def _native_class(base: Any, receiver: _Receiver, channel: str) -> Any:
    def connected(_self: object) -> None:
        receiver.event(channel, "OnFrontConnected")

    def disconnected(_self: object, reason: int) -> None:
        receiver.event(channel, "OnFrontDisconnected", {"Reason": reason})

    def heartbeat(_self: object, lapse: int) -> None:
        receiver.event(channel, "OnHeartBeatWarning", {"TimeLapse": lapse})

    def error(_self: object, native: object, request: int, last: bool) -> None:
        receiver.callback(channel, "OnRspError", None, native, request, last)

    methods: dict[str, Any] = {
        "OnFrontConnected": connected,
        "OnFrontDisconnected": disconnected,
        "OnHeartBeatWarning": heartbeat,
        "OnRspError": error,
        "OnRspUserLogin": _response(receiver, channel, "OnRspUserLogin"),
    }
    if channel == "TD":
        for suffix in ("Authenticate", *("Qry" + query for _, query in _TD_QUERIES)):
            callback = "OnRsp" + suffix
            methods[callback] = _response(receiver, channel, callback)
        for callback in ("OnRtnOrder", "OnRtnTrade"):
            methods[callback] = _notification(receiver, channel, callback)
    else:
        methods["OnRspSubMarketData"] = _response(receiver, channel, "OnRspSubMarketData")
        methods["OnRtnDepthMarketData"] = _notification(receiver, channel, "OnRtnDepthMarketData")
    return type("Readonly" + channel, (base,), methods)


def _same_account(
    receiver: _Receiver, credentials: Credentials, channel: str, request: int
) -> bool:
    response = receiver.responses.get((channel, "OnRspUserLogin", request))
    if (
        response is None
        or response.get("BrokerID") != credentials.broker_id
        or response.get("UserID") != credentials.user_id
        or not str(response.get("TradingDay", "")).isdigit()
        or len(str(response.get("TradingDay", ""))) != 8
    ):
        receiver.failure = "LOGIN_IDENTITY_MISMATCH"
        return False
    return True


def _silence_native() -> None:
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)


def _versions(trader: Any, market: Any) -> dict[str, str]:
    result = {}
    for key, api in (("trader", trader), ("market", market)):
        value = api.GetApiVersion()
        version = value.decode("ascii") if isinstance(value, bytes) else str(value)
        if not version.startswith("v6.7.13_"):
            raise ValueError("native SDK does not match the selected binding")
        result[key] = version
    return result


def check_native(connection: Connection) -> None:
    """A separate no-network code path: no credentials, fronts, Init or login."""

    _silence_native()
    result: dict[str, object] = {
        "native_verified": False,
        "trader_api_version": None,
        "market_api_version": None,
        "reason": None,
    }
    trader = market = None
    try:
        sdk = importlib.import_module("ctpwrapper")
        with tempfile.TemporaryDirectory(prefix="northstar-ctp-check-") as directory:
            trader, market = sdk.TraderApiPy(), sdk.MdApiPy()
            trader.Create(directory + "/td-")
            market.Create(directory + "/md-")
            versions = _versions(trader, market)
            trader.Release()
            market.Release()
            result.update(
                native_verified=True,
                trader_api_version=versions["trader"],
                market_api_version=versions["market"],
            )
    except Exception:
        result["reason"] = "SDK_SELF_CHECK_FAILED"
    finally:
        connection.send_bytes(json.dumps(result).encode())
        connection.close()


def capture(
    connection: Connection,
    profile: SimnowProfile,
    credentials: Credentials,
    instrument: str,
    directory: str,
    timeout: float,
) -> None:
    # Native libraries can print directly through C stdio. Suppress both output
    # descriptors before importing them; only our typed evidence leaves the child.
    _silence_native()
    receiver = _Receiver(connection, timeout)
    receiver.event(
        "TD",
        "CaptureStarted",
        {
            "profile_name": profile.name,
            "td_front": profile.td_front,
            "md_front": profile.md_front,
            "broker_id": credentials.broker_id,
            "account_id": credentials.user_id,
            "instrument": instrument,
        },
    )
    receiver.drain()
    trader = market = None
    try:
        sdk = importlib.import_module("ctpwrapper")
        structures = importlib.import_module("ctpwrapper.ApiStructure")
        trader = _native_class(sdk.TraderApiPy, receiver, "TD")()
        market = _native_class(sdk.MdApiPy, receiver, "MD")()
        connection.send_bytes(
            json.dumps({"kind": "versions", **_versions(trader, market)}).encode()
        )
        trader.Create(directory + "/td-")
        market.Create(directory + "/md-")
        trader.RegisterFront(profile.td_front)
        # Observe current reports without replaying previous SDK flow state.
        trader.SubscribePrivateTopic(2)
        trader.SubscribePublicTopic(2)
        trader.Init()
        if not receiver.wait(lambda: "TD" in receiver.connected):
            receiver.failure = receiver.failure or "TD_CONNECT_TIMEOUT"
            return
        auth = structures.ReqAuthenticateField(
            BrokerID=credentials.broker_id,
            UserID=credentials.user_id,
            AppID=credentials.app_id,
            AuthCode=credentials.auth_code,
        )
        if not receiver.request(trader, "authenticate", "Authenticate", auth, 1, "TD"):
            return
        auth_result = receiver.responses.get(("TD", "OnRspAuthenticate", 1))
        if not auth_result or any(
            auth_result.get(key) != expected
            for key, expected in (
                ("BrokerID", credentials.broker_id),
                ("UserID", credentials.user_id),
            )
        ):
            receiver.failure = "AUTHENTICATION_IDENTITY_MISMATCH"
            return
        login = structures.ReqUserLoginField(
            BrokerID=credentials.broker_id,
            UserID=credentials.user_id,
            Password=credentials.password,
        )
        if not receiver.request(trader, "login", "UserLogin", login, 2, "TD"):
            return
        if not _same_account(receiver, credentials, "TD", 2):
            return
        # One in-flight query at a time, with a conservative 1.1-second gap.
        # This is our limit, not a claim about the current broker's configured cap.
        for request, (section, suffix) in enumerate(_TD_QUERIES, start=3):
            ready_at = time.monotonic() + _QUERY_INTERVAL
            if not receiver.wait(lambda: time.monotonic() >= ready_at):
                receiver.failure = receiver.failure or "QUERY_TIMEOUT"
                return
            fields = (
                {}
                if section == "instrument"
                else {
                    "BrokerID": credentials.broker_id,
                    "InvestorID": credentials.user_id,
                }
            )
            if section == "account":
                fields["CurrencyID"] = "CNY"
            if section in {"instrument", "margin", "commission"}:
                fields["InstrumentID"] = instrument
            if section == "margin":
                fields["HedgeFlag"] = "1"
            native = getattr(structures, "Qry" + suffix + "Field")(**fields)
            if not receiver.request(trader, section, "Qry" + suffix, native, request, "TD"):
                return
        market.RegisterFront(profile.md_front)
        market.Init()
        if not receiver.wait(lambda: "MD" in receiver.connected, seconds=5):
            receiver.failure = receiver.failure or "MD_CONNECT_TIMEOUT"
            return
        if not receiver.request(market, "login", "UserLogin", login, 100, "MD"):
            return
        if not _same_account(receiver, credentials, "MD", 100):
            return
        pending = receiver.event(
            "MD",
            "RequestSent",
            {
                "section": "depth",
                "method": "SubscribeMarketData",
                "return_code": None,
            },
        )
        if pending is None:
            return
        code = int(market.SubscribeMarketData([instrument]))
        cast(dict[str, object], pending["data"])["return_code"] = code
        if code:
            receiver.failure = "CTP_SUBSCRIPTION_REJECTED"
            return
        # SubscribeMarketData has no request-ID input: keep its actual callback
        # identity instead of inventing a caller request ID.
        receiver.wait(lambda: receiver.quote_seen, seconds=3)
    except Exception:
        receiver.failure = receiver.failure or "SDK_OPERATION_FAILED"
    finally:
        receiver.drain()
        connection.send_bytes(
            json.dumps(
                {
                    "kind": "releasing",
                    "failure_code": receiver.failure,
                }
            ).encode()
        )
        # Keep draining callbacks produced during Release. If native joins while
        # holding the GIL, the parent kills only this bounded read-only worker.
        try:
            if market is not None:
                market.Release()
            if trader is not None:
                trader.Release()
        except Exception:
            receiver.failure = receiver.failure or "SDK_RELEASE_FAILED"
        with receiver.lock:
            receiver.closed = True
        receiver.drain()
        connection.send_bytes(
            json.dumps(
                {
                    "kind": "complete",
                    "failure_code": receiver.failure,
                }
            ).encode()
        )
        connection.close()
