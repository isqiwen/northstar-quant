"""The public native capture must retain facts without authority or secret leaks.

The child receives a scripted CTP implementation, never a reachable SDK. Real
SDK load/create/release is separately checked in an explicitly networkless image.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import struct
import threading
import time
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from northstar_quant.broker import _ctp_worker, ctp
from northstar_quant.broker.records import BrokerEvent, BrokerRecords
from northstar_quant.broker.settings import Credentials, SimnowProfile, get_profile


class _Trader:
    """Only read requests exist; an accidental write operation immediately fails."""

    def GetApiVersion(self) -> str:
        return "v6.7.13_scripted_test"

    def Create(self, _path: str) -> None:
        pass

    def RegisterFront(self, _front: str) -> None:
        pass

    def SubscribePrivateTopic(self, mode: int, sequence: int) -> None:
        assert (mode, sequence) == (2, 1)

    def SubscribePublicTopic(self, _mode: int) -> None:
        pass

    def Init(self) -> None:
        self.OnFrontConnected()

    def Release(self) -> None:
        pass

    def __getattr__(self, method: str) -> Any:
        permitted = {
            "ReqAuthenticate",
            "ReqUserLogin",
            "ReqQryTradingAccount",
            "ReqQryInvestorPosition",
            "ReqQryOrder",
            "ReqQryTrade",
            "ReqQryInstrument",
            "ReqQryInstrumentMarginRate",
            "ReqQryInstrumentCommissionRate",
        }
        if method not in permitted:
            raise AssertionError("unexpected SDK operation")

        def respond(native: SimpleNamespace, request: int) -> int:
            if method == "ReqQryTradingAccount":
                assert native.BizType == "1"
            if method == "ReqQryOrder" and _MODE == "reject":
                return -3
            callback = getattr(self, "OnRsp" + method.removeprefix("Req"))
            values = dict(vars(native))
            values.update(
                TradingDay="20260904",
                BrokerID="9999",
                UserID="123456",
                InvestorID="123456",
                AccountID="123456",
                CurrencyID="CNY",
                Balance=100000.0,
                Available=100000.0,
                Password="NEVER-PERSIST-PASSWORD",
                AuthCode="NEVER-PERSIST-AUTH",
            )
            if method == "ReqQryInvestorPosition":
                callback(None, None, request, _MODE != "missing_last")
            else:
                callback(SimpleNamespace(**values), None, request, True)
            if method == "ReqQryOrder":
                self.OnRtnTrade(SimpleNamespace(**values))
            return 0

        return respond


class _Market(_Trader):
    def ReqUserLogin(self, _native: SimpleNamespace, _request: int) -> int:
        # Actual SimNow MD reply: request 0, no account identity. It is not TD
        # authentication, and must not erase the independently confirmed account.
        self.OnRspUserLogin(
            SimpleNamespace(
                TradingDay="20260903" if _MODE == "market_day" else "20260904",
                BrokerID="9999" if _MODE == "market_account" else "",
                UserID="654321" if _MODE == "market_account" else "",
            ),
            None,
            0,
            True,
        )
        return 0

    def SubscribeMarketData(self, instruments: list[str]) -> int:
        self.OnRspSubMarketData(SimpleNamespace(InstrumentID=instruments[0]), None, 0, True)
        for price in (float("inf"), 100.0):
            self.OnRtnDepthMarketData(
                SimpleNamespace(
                    InstrumentID=instruments[0],
                    LastPrice=price,
                    Password="NEVER-PERSIST-PASSWORD",
                )
            )
        return 0


_MODE = "normal"


class _StreamMarket(_Market):
    def SubscribeMarketData(self, instruments: list[str]) -> int:
        super().SubscribeMarketData(instruments)
        if _MODE == "overflow":
            for _ in range(1000):
                self.OnRtnDepthMarketData(
                    SimpleNamespace(InstrumentID=instruments[0], LastPrice=101.0)
                )
            return 0
        self._closed = threading.Event()

        def emit() -> None:
            for index in range(1, 6):
                if self._closed.wait(0.04):
                    return
                self.OnRtnDepthMarketData(
                    SimpleNamespace(InstrumentID=instruments[0], LastPrice=100.0 + index)
                )
            if _MODE == "disconnect":
                self.OnFrontDisconnected(4097)
                self.OnFrontConnected()  # SDK reconnect never causes another login.
            elif _MODE == "heartbeat":
                self.OnHeartBeatWarning(30)

        self._emitter = threading.Thread(target=emit, daemon=True)
        self._emitter.start()
        return 0

    def Release(self) -> None:
        if "_closed" in self.__dict__:
            self._closed.set()
            self._emitter.join(timeout=0.2)


def _scripted_capture(
    connection: Connection,
    profile: SimnowProfile,
    credentials: Credentials,
    instrument: str,
    directory: str,
    timeout: float,
) -> None:
    _install_scripted(instrument)
    _ctp_worker.capture(connection, profile, credentials, instrument, directory, timeout)


def _install_scripted(instrument: str, *, streaming: bool = False) -> None:
    global _MODE
    _MODE = {
        "er2610": "reject",
        "ml2610": "missing_last",
        "md2610": "market_day",
        "ma2610": "market_account",
        "dc2610": "disconnect",
        "hb2610": "heartbeat",
        "of2610": "overflow",
    }.get(instrument, "normal")
    sdk = SimpleNamespace(TraderApiPy=_Trader, MdApiPy=_StreamMarket if streaming else _Market)
    structures = SimpleNamespace(
        **{
            name: SimpleNamespace
            for name in (
                "ReqAuthenticateField",
                "ReqUserLoginField",
                "QryTradingAccountField",
                "QryInvestorPositionField",
                "QryOrderField",
                "QryTradeField",
                "QryInstrumentField",
                "QryInstrumentMarginRateField",
                "QryInstrumentCommissionRateField",
            )
        }
    )
    setattr(
        _ctp_worker,
        "importlib",
        SimpleNamespace(
            import_module=lambda name: sdk if name == "ctpwrapper" else structures,
        ),
    )
    _ctp_worker._QUERY_INTERVAL = 0


def _scripted_stream(
    connection: Connection,
    profile: SimnowProfile,
    credentials: Credentials,
    instrument: str,
    directory: str,
    duration: float,
    stop_signal: Any,
) -> None:
    _install_scripted(instrument, streaming=True)
    _ctp_worker.stream(
        connection, profile, credentials, instrument, directory, duration, stop_signal
    )


def _crashed_stream(
    connection: Connection,
    profile: SimnowProfile,
    credentials: Credentials,
    instrument: str,
    directory: str,
    duration: float,
    _stop_signal: Any,
) -> None:
    if instrument == "pc2610":
        connection.send_bytes(
            json.dumps(
                {"kind": "versions", "trader": "v6.7.13_test", "market": "v6.7.13_test"}
            ).encode()
        )
        os.write(connection.fileno(), struct.pack("!i", 1024))
        time.sleep(60)  # Length prefix arrived, body never does.
        return
    _crashed_capture(connection, profile, credentials, instrument, directory, duration)


def _crashed_capture(
    connection: Connection,
    _profile: SimnowProfile,
    _credentials: Credentials,
    instrument: str,
    _directory: str,
    _timeout: float,
) -> None:
    event = BrokerEvent(
        1,
        "TD",
        "OnFrontConnected",
        None,
        None,
        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        0,
        None,
    )
    connection.send_bytes(json.dumps({"kind": "event", "event": event.to_dict()}).encode())
    if instrument == "to2610":
        time.sleep(60)
    os._exit(23)


def _available(monkeypatch: pytest.MonkeyPatch, worker: Any) -> None:
    monkeypatch.setattr(
        ctp,
        "sdk_status",
        lambda: {
            "available": True,
            "binding_version": "6.7.13",
            "reason": None,
        },
    )
    monkeypatch.setattr(_ctp_worker, "capture", worker)


def _credentials() -> Credentials:
    return Credentials("123456", "local-test-password", "test-app", "test-auth")


def test_capture_orders_fast_callbacks_and_copies_only_permitted_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _available(monkeypatch, _scripted_capture)
    result = ctp.query_account(
        get_profile("simnow_dev"), _credentials(), "rb2610", timeout_seconds=1
    )
    assert result.failure_code is None
    assert result.trader_api_version == "v6.7.13_scripted_test"
    requests = [event for event in result.events if event.callback == "RequestSent"]
    assert [
        event.data["section"]
        for event in requests
        if event.channel == "TD" and event.data is not None
    ] == [
        "authenticate",
        "login",
        "account",
        "positions",
        "orders",
        "trades",
        "instrument",
        "margin",
        "commission",
    ]
    for request in requests:
        assert request.data is not None
        assert request.data["return_code"] == 0
        if request.channel == "TD":
            response = next(
                event
                for event in result.events
                if (
                    event.channel == "TD"
                    and event.request_id == request.request_id
                    and event.callback != "RequestSent"
                )
            )
            assert request.sequence < response.sequence
            assert request.received_at <= response.received_at
    assert any(event.callback == "OnRtnTrade" for event in result.events)
    quotes = [event for event in result.events if event.callback == "OnRtnDepthMarketData"]
    assert len(quotes) == 1 and quotes[0].data is not None
    assert quotes[0].data["LastPrice"] is None
    assert requests[-1].request_id is None  # SDK subscription has no request-id argument.
    evidence = json.dumps(result.to_dict())
    assert "NEVER-PERSIST" not in evidence and "local-test-password" not in evidence


@pytest.mark.parametrize(
    "instrument, failure",
    [
        ("er2610", "CTP_REQUEST_REJECTED"),
        ("ml2610", "QUERY_TIMEOUT"),
        ("md2610", "MARKET_LOGIN_TRADING_DAY_MISMATCH"),
        ("ma2610", "MARKET_LOGIN_IDENTITY_MISMATCH"),
    ],
)
def test_rejected_or_unterminated_query_keeps_evidence_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    instrument: str,
    failure: str,
) -> None:
    _available(monkeypatch, _scripted_capture)
    result = ctp.query_account(
        get_profile("simnow_dev"),
        _credentials(),
        instrument,
        timeout_seconds=1,
    )
    assert result.failure_code == failure
    requests = [event for event in result.events if event.callback == "RequestSent"]
    assert len({event.request_id for event in requests}) == len(requests)
    if instrument in {"er2610", "ml2610"}:
        assert not any(
            event.data["section"] == "commission" for event in requests if event.data is not None
        )
    else:
        assert not any(event.callback == "OnRspSubMarketData" for event in result.events)
    if instrument == "er2610":
        assert requests[-1].data is not None
        assert requests[-1].data["return_code"] == -3


@pytest.mark.parametrize(
    "instrument, failure",
    [
        ("cr2610", "SDK_PROCESS_EXITED"),
        ("to2610", "CAPTURE_TIMEOUT"),
    ],
)
def test_native_crash_or_hang_returns_received_facts_and_reaps_its_process(
    monkeypatch: pytest.MonkeyPatch,
    instrument: str,
    failure: str,
) -> None:
    _available(monkeypatch, _crashed_capture)
    started = time.monotonic()
    result = ctp.query_account(
        get_profile("simnow_dev"),
        _credentials(),
        instrument,
        timeout_seconds=1,
    )
    assert time.monotonic() - started < 8
    assert result.failure_code == failure
    assert [event.callback for event in result.events] == ["OnFrontConnected"]


def test_unapproved_endpoint_is_rejected_before_any_sdk_activity() -> None:
    wrong = SimnowProfile("simnow_dev", "tcp://127.0.0.1:1234", "tcp://127.0.0.1:1235")
    with pytest.raises(ValueError, match="approved"):
        ctp.query_account(wrong, _credentials(), "rb2610")
    with pytest.raises(ValueError, match="approved"):
        ctp.stream_account(
            wrong,
            _credentials(),
            "rb2610",
            on_event=lambda event: None,
            should_stop=lambda: False,
            duration_seconds=1,
        )


def test_scripted_capture_round_trips_postgres_without_claiming_external_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    """Synthetic SDK callbacks plus real PostgreSQL, never external broker evidence."""

    del clean_database
    _available(monkeypatch, _scripted_capture)
    profile, credentials, request_id = get_profile("simnow_dev"), _credentials(), uuid4()
    records = BrokerRecords(postgres_engine)
    pending = records.begin(
        profile.identity(),
        credentials.user_id,
        "rb2610",
        request_id=request_id,
    )
    assert pending["status"] == "PENDING"
    capture = ctp.query_account(profile, credentials, "rb2610")
    saved = records.finish(request_id, capture)
    assert BrokerRecords(postgres_engine).get(request_id) == saved
    assert records.finish(request_id, capture) == saved
    assert saved["capture"] == capture.to_dict()
    assert saved["profile"] == profile.identity() and saved["account_id"] == credentials.user_id
    completeness = cast(dict[str, Any], saved["completeness"])
    assert saved["status"] == "COMPLETE"
    assert completeness["identity"] == "CONFIRMED"
    assert completeness["trading_day"] == "20260904"
    sections = completeness["sections"]
    assert len(sections) == 7 and all(item["status"] == "COMPLETE" for item in sections.values())
    assert sections["account"]["rows"][0]["Balance"] == "100000.0"
    assert sections["positions"]["rows"] == []
    assert sections["instrument"]["rows"][0]["InstrumentID"] == "rb2610"
    reconciliation = cast(dict[str, object], saved["reconciliation"])
    assert reconciliation["status"] == "UNRECONCILED"
    assert reconciliation["local_ledger"] == "NOT_ESTABLISHED"
    assert reconciliation["differences"] is None
    assert saved["execution"] == {"order_sending": False, "cancel_sending": False}
    assert "NEVER-PERSIST" not in json.dumps(saved)


def test_stream_persists_successive_quotes_and_stops_without_reconnecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _available(monkeypatch, _scripted_capture)
    monkeypatch.setattr(_ctp_worker, "stream", _scripted_stream)
    events: list[BrokerEvent] = []
    quotes = 0

    def retain(event: BrokerEvent) -> None:
        nonlocal quotes
        events.append(event)
        if event.callback == "OnRtnDepthMarketData":
            quotes += 1

    failure = ctp.stream_account(
        get_profile("simnow_dev"),
        _credentials(),
        "rb2610",
        on_event=retain,
        should_stop=lambda: quotes >= 4,
        duration_seconds=10,
    )
    assert failure is None and quotes >= 4
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert any(event.callback == "OnRtnTrade" for event in events)
    requests = [event for event in events if event.callback == "RequestSent"]
    assert (
        len([event for event in requests if event.data and event.data["section"] == "login"]) == 2
    )
    serialized = json.dumps([event.to_dict() for event in events])
    assert "NEVER-PERSIST" not in serialized and "local-test-password" not in serialized
    assert not [
        child for child in multiprocessing.active_children() if child.name == "northstar-ctp-stream"
    ]


@pytest.mark.parametrize(
    "instrument, expected",
    [
        ("dc2610", "DISCONNECTED"),
        ("hb2610", "HEARTBEAT_WARNING"),
        ("of2610", "STREAM_LIMIT_EXCEEDED"),
    ],
)
def test_stream_stops_on_disconnect_heartbeat_or_callback_backpressure(
    monkeypatch: pytest.MonkeyPatch,
    instrument: str,
    expected: str,
) -> None:
    _available(monkeypatch, _scripted_capture)
    monkeypatch.setattr(_ctp_worker, "stream", _scripted_stream)
    events: list[BrokerEvent] = []
    failure = ctp.stream_account(
        get_profile("simnow_dev"),
        _credentials(),
        instrument,
        on_event=events.append,
        should_stop=lambda: False,
        duration_seconds=5,
    )
    assert failure == expected
    assert len([event for event in events if event.callback == "OnRspAuthenticate"]) == 1
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


@pytest.mark.parametrize("callback", ["record", "stop"])
def test_stream_propagates_failed_caller_callbacks_and_reaps_native_process(
    monkeypatch: pytest.MonkeyPatch,
    callback: str,
) -> None:
    _available(monkeypatch, _scripted_capture)
    monkeypatch.setattr(_ctp_worker, "stream", _scripted_stream)
    recorded = 0
    failure = ValueError("synthetic durable write failure")

    def retain(event: BrokerEvent) -> None:
        nonlocal recorded
        recorded += 1
        if callback == "record":
            raise failure

    def stop() -> bool:
        if callback == "stop" and recorded:
            raise failure
        return False

    with pytest.raises(ValueError) as caught:
        ctp.stream_account(
            get_profile("simnow_dev"),
            _credentials(),
            "rb2610",
            on_event=retain,
            should_stop=stop,
            duration_seconds=5,
        )
    assert caught.value is failure and recorded == 1
    assert not [
        child for child in multiprocessing.active_children() if child.name == "northstar-ctp-stream"
    ]


@pytest.mark.parametrize(
    "instrument, expected",
    [
        ("cr2610", "SDK_PROCESS_EXITED"),
        ("to2610", "STREAM_STOP_TIMEOUT"),
        ("pc2610", "STREAM_STOP_TIMEOUT"),
    ],
)
def test_stream_crash_or_unresponsive_stop_does_not_leave_native_child_running(
    monkeypatch: pytest.MonkeyPatch,
    instrument: str,
    expected: str,
) -> None:
    _available(monkeypatch, _scripted_capture)
    monkeypatch.setattr(_ctp_worker, "stream", _crashed_stream)
    events: list[BrokerEvent] = []
    started = time.monotonic()
    failure = ctp.stream_account(
        get_profile("simnow_dev"),
        _credentials(),
        instrument,
        on_event=events.append,
        should_stop=lambda: bool(events),
        duration_seconds=1,
    )
    assert time.monotonic() - started < 6
    assert failure == expected
    assert [event.callback for event in events] == (
        [] if instrument == "pc2610" else ["OnFrontConnected"]
    )
    assert not [
        child for child in multiprocessing.active_children() if child.name == "northstar-ctp-stream"
    ]
