"""The public native capture must retain facts without authority or secret leaks.

The child receives a scripted CTP implementation, never a reachable SDK. Real
SDK load/create/release is separately checked in an explicitly networkless image.
"""

from __future__ import annotations

import json
import os
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

    def SubscribePrivateTopic(self, _mode: int) -> None:
        pass

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


def _scripted_capture(
    connection: Connection,
    profile: SimnowProfile,
    credentials: Credentials,
    instrument: str,
    directory: str,
    timeout: float,
) -> None:
    global _MODE
    _MODE = {"er2610": "reject", "ml2610": "missing_last"}.get(instrument, "normal")
    sdk = SimpleNamespace(TraderApiPy=_Trader, MdApiPy=_Market)
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
    _ctp_worker.capture(connection, profile, credentials, instrument, directory, timeout)


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
    result = ctp.query_account(get_profile("simnow_dev"), _credentials(), "rb2610")
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
    assert not any(
        event.data["section"] == "commission" for event in requests if event.data is not None
    )
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
