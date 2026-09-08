"""Synthetic callback inputs test PostgreSQL evidence, never external broker acceptance."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from northstar_quant.broker.records import BrokerEvent, BrokerRecords, QueryCapture
from northstar_quant.broker.settings import get_profile


def _capture(
    *,
    profile: str = "simnow_dev",
    account: str = "123456",
    with_position: bool = False,
    instruments: tuple[str, ...] = ("rb2610",),
) -> QueryCapture:
    started = datetime.now(UTC)
    events: list[BrokerEvent] = []

    def event(
        callback: str,
        data: dict[str, object] | None = None,
        *,
        request: int | None = None,
        last: bool | None = None,
        channel: str = "TD",
    ) -> None:
        sequence = len(events) + 1
        received = (started + timedelta(microseconds=sequence)).isoformat().replace("+00:00", "Z")
        events.append(BrokerEvent(sequence, channel, callback, request, last, received, 0, data))

    def sent(section: str, method: str, request: int | None, channel: str = "TD") -> None:
        event(
            "RequestSent",
            {"section": section, "method": method, "return_code": 0},
            request=request,
            channel=channel,
        )

    selected = get_profile(profile).identity()
    event(
        "CaptureStarted",
        {
            "profile_name": selected["name"],
            "td_front": selected["td_front"],
            "md_front": selected["md_front"],
            "broker_id": selected["broker_id"],
            "account_id": account,
            "instrument": "rb2610",
        },
    )
    event("OnFrontConnected")
    sent("authenticate", "ReqAuthenticate", 1)
    event("OnRspAuthenticate", {"BrokerID": "9999", "UserID": account}, request=1, last=True)
    sent("login", "ReqUserLogin", 2)
    login = {
        "BrokerID": "9999",
        "UserID": account,
        "TradingDay": "20260907",
        "FrontID": 7,
        "SessionID": 11,
    }
    event("OnRspUserLogin", login, request=2, last=True)
    position = {
        "BrokerID": "9999",
        "InvestorID": account,
        "InstrumentID": "cu2610",
        "ExchangeID": "SHFE",
        "TradingDay": "20260907",
        "PosiDirection": "2",
        "HedgeFlag": "1",
        "PositionDate": "1",
        "Position": 2,
        "TodayPosition": 1,
        "YdPosition": 1,
    }
    rows: dict[str, dict[str, object] | None] = {
        "TradingAccount": {
            "BrokerID": "9999",
            "AccountID": account,
            "CurrencyID": "CNY",
            "TradingDay": "20260907",
            "Balance": "100000",
            "Available": "95000",
            "CurrMargin": "5000",
            "Password": "must-not-retain-secret",
        },
        "InvestorPosition": position if with_position else None,
        "Order": None,
        "Trade": None,
        "Instrument": {
            "InstrumentID": "rb2610",
            "ExchangeID": "SHFE",
            "VolumeMultiple": 10,
            "PriceTick": "1",
            "IsTrading": True,
        },
        "InstrumentMarginRate": {
            "InstrumentID": "rb2610",
            "BrokerID": "9999",
            "InvestorID": account,
            "HedgeFlag": "1",
            "LongMarginRatioByMoney": "0.12",
        },
        "InstrumentCommissionRate": {
            "InstrumentID": "rb2610",
            "BrokerID": "9999",
            "InvestorID": account,
            "OpenRatioByMoney": "0.0001",
        },
    }
    for request, (section, suffix) in enumerate(
        (
            ("account", "TradingAccount"),
            ("positions", "InvestorPosition"),
            ("orders", "Order"),
            ("trades", "Trade"),
            ("instrument", "Instrument"),
            ("margin", "InstrumentMarginRate"),
            ("commission", "InstrumentCommissionRate"),
        ),
        10,
    ):
        sent(section, "ReqQry" + suffix, request)
        if section == "instrument" and instruments:
            assert rows[suffix] is not None
            for index, name in enumerate(instruments):
                event(
                    "OnRspQry" + suffix,
                    {**rows[suffix], "InstrumentID": name},
                    request=request,
                    last=index == len(instruments) - 1,
                )
        else:
            event(
                "OnRspQry" + suffix,
                None if section == "instrument" else rows[suffix],
                request=request,
                last=True,
            )
    event("OnFrontConnected", channel="MD")
    sent("login", "ReqUserLogin", 0, channel="MD")
    event("OnRspUserLogin", login, request=0, last=True, channel="MD")
    sent("depth", "SubscribeMarketData", None, channel="MD")
    event("OnRspSubMarketData", {"InstrumentID": "rb2610"}, request=0, last=True, channel="MD")
    event(
        "OnRtnDepthMarketData",
        {
            "InstrumentID": "rb2610",
            "TradingDay": "20260907",
            "LastPrice": "3100",
            "UpperLimitPrice": None,
        },
        channel="MD",
    )
    return QueryCapture(
        started.isoformat().replace("+00:00", "Z"),
        (started + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        "test-callback-input",
        "1",
        "test-api",
        "test-api",
        tuple(events),
    )


def _begin(
    records: BrokerRecords,
    *,
    profile: str = "simnow_dev",
    account: str = "123456",
    request_id: UUID | None = None,
) -> dict[str, object]:
    return records.begin(
        get_profile(profile).identity(),
        account,
        "rb2610",
        request_id=uuid4() if request_id is None else request_id,
    )


def test_query_completion_is_durable_private_and_never_establishes_a_ledger(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    request_id = uuid4()
    pending = _begin(records, request_id=request_id)
    assert pending["status"] == "PENDING"
    assert pending["completeness"]["sections"]["account"]["rows"] is None
    assert _begin(records, request_id=request_id) == pending
    capture = _capture(with_position=True)
    saved = records.finish(request_id, capture)
    assert saved["status"] == "COMPLETE"
    assert saved["reconciliation"]["status"] == "UNRECONCILED"
    assert saved["reconciliation"]["local_ledger"] == "NOT_ESTABLISHED"
    assert saved["reconciliation"]["differences"] is None
    assert saved["execution"] == {"order_sending": False, "cancel_sending": False}
    assert saved["completeness"]["sections"]["orders"]["rows"] == []
    # The scope is the whole account, not just the selected instrument.
    assert saved["completeness"]["sections"]["positions"]["rows"][0]["InstrumentID"] == "cu2610"
    assert saved["completeness"]["sections"]["positions"]["rows"][0]["YdPosition"] == 1
    assert saved["market"]["continuous_feed"] is False
    assert saved["market"]["login_identity"] == "CONFIRMED"
    assert saved["market"]["depth"]["data"]["UpperLimitPrice"] is None
    assert "must-not-retain-secret" not in json.dumps(saved)
    assert "Password" not in json.dumps(saved)
    assert records.finish(request_id, capture) == saved
    assert _begin(records, request_id=request_id) == saved
    reopened = create_engine(postgres_engine.url)
    try:
        assert BrokerRecords(reopened).get(request_id) == saved
    finally:
        reopened.dispose()
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM broker_query_batches")) == 1
        assert connection.scalar(text("SELECT count(*) FROM paper_sessions")) == 0


def test_requests_bind_account_and_environment_without_fallback_or_implicit_reconnect(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    request_id = uuid4()
    first = _begin(records, request_id=request_id)
    for profile, account in (("simnow_trading", "123456"), ("simnow_dev", "654321")):
        with pytest.raises(ValueError, match="different input"):
            _begin(records, profile=profile, account=account, request_id=request_id)
    second = _begin(records, profile="simnow_trading")
    assert [
        item["batch_id"] for item in records.list(profile_name="simnow_dev", account_id="123456")
    ] == [first["batch_id"]]
    assert [
        item["batch_id"]
        for item in records.list(profile_name="simnow_trading", account_id="123456")
    ] == [second["batch_id"]]
    assert "capture" not in records.list()[0]
    mixed = dict(
        get_profile("simnow_dev").identity(), md_front=get_profile("simnow_trading").md_front
    )
    with pytest.raises(ValueError, match="approved"):
        records.begin(mixed, "123456", "rb2610", request_id=uuid4())
    assert records.get(request_id)["status"] == "PENDING"
    with pytest.raises(ValueError, match="ASCII"):
        _begin(records, account="１２３")


def test_market_login_without_account_identity_preserves_confirmed_td_account(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    batch_id = UUID(str(_begin(records)["batch_id"]))
    capture = _capture()
    events = tuple(
        replace(event, data={**event.data, "BrokerID": "", "UserID": ""})
        if event.channel == "MD" and event.callback == "OnRspUserLogin" and event.data
        else event
        for event in capture.events
    )
    saved = records.finish(batch_id, replace(capture, events=events))
    assert saved["status"] == "COMPLETE"
    assert saved["completeness"]["identity"] == "CONFIRMED"
    assert saved["market"]["login_identity"] == "UNKNOWN"
    assert saved["market"]["login"]["UserID"] == ""
    assert "MARKET_LOGIN_IDENTITY_UNKNOWN" in saved["reconciliation"]["reasons"]
    assert saved["reconciliation"]["status"] == "UNRECONCILED"
    assert saved["execution"] == {"order_sending": False, "cancel_sending": False}
    assert records.get(batch_id) == saved


@pytest.mark.parametrize(
    "failure, expected_status",
    [
        ("wrong_request", "INCOMPLETE"),
        ("missing_last", "INCOMPLETE"),
        ("broker_error", "FAILED"),
        ("wrong_account", "FAILED"),
        ("wrong_day", "FAILED"),
        ("missing_day", "FAILED"),
        ("capture_failure", "FAILED"),
    ],
)
def test_market_uncertainty_cannot_validate_the_query_or_overwrite_td_identity(
    postgres_engine: Engine,
    clean_database: None,
    failure: str,
    expected_status: str,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    batch_id = UUID(str(_begin(records)["batch_id"]))
    capture = _capture()
    events = []
    for event in capture.events:
        if event.channel == "MD" and event.callback == "OnRspUserLogin":
            assert event.data is not None
            if failure == "wrong_request":
                event = replace(event, request_id=100)
            elif failure == "missing_last":
                event = replace(event, is_last=False)
            elif failure == "broker_error":
                event = replace(event, error_id=42)
            elif failure == "wrong_account":
                event = replace(event, data={**event.data, "UserID": "654321"})
            elif failure in {"wrong_day", "missing_day"}:
                event = replace(
                    event,
                    data={
                        **event.data,
                        "TradingDay": "20260904" if failure == "wrong_day" else None,
                    },
                )
        events.append(event)
    saved = records.finish(
        batch_id,
        replace(
            capture,
            events=tuple(events),
            failure_code="QUERY_TIMEOUT" if failure == "capture_failure" else None,
        ),
    )
    assert saved["status"] == expected_status
    assert saved["completeness"]["identity"] == "CONFIRMED"
    assert saved["market"]["login_identity"] == (
        "MISMATCH"
        if failure == "wrong_account"
        else "UNKNOWN"
        if failure in {"wrong_request", "missing_last", "broker_error"}
        else "CONFIRMED"
    )
    if failure in {"wrong_day", "missing_day"}:
        assert "MARKET_LOGIN_TRADING_DAY_MISMATCH" in saved["completeness"]["reasons"]
    assert saved["reconciliation"]["status"] == "UNRECONCILED"
    assert saved["execution"] == {"order_sending": False, "cancel_sending": False}


def test_exact_contract_projection_keeps_all_broker_prefix_matches_as_evidence(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    batch_id = UUID(str(_begin(records)["batch_id"]))
    capture = _capture(instruments=("rb2610C3500", "rb2610", "rb2610P3300"))
    saved = records.finish(batch_id, capture)
    assert saved["status"] == "COMPLETE"
    section = saved["completeness"]["sections"]["instrument"]
    assert section["identity"] == "CONFIRMED"
    assert [row["InstrumentID"] for row in section["rows"]] == ["rb2610"]
    assert [
        event["data"]["InstrumentID"]
        for event in saved["capture"]["events"]
        if event["callback"] == "OnRspQryInstrument"
    ] == ["rb2610C3500", "rb2610", "rb2610P3300"]
    assert saved["reconciliation"]["status"] == "UNRECONCILED"
    assert records.get(batch_id) == saved


@pytest.mark.parametrize("instruments", [(), ("rb2610C3500",), ("rb2610", "rb2610")])
def test_missing_or_duplicate_exact_contract_cannot_confirm_selected_terms(
    postgres_engine: Engine,
    clean_database: None,
    instruments: tuple[str, ...],
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    batch_id = UUID(str(_begin(records)["batch_id"]))
    saved = records.finish(batch_id, _capture(instruments=instruments))
    assert saved["status"] == "INCOMPLETE"
    section = saved["completeness"]["sections"]["instrument"]
    assert section["status"] == "COMPLETE"  # Response ended, contract remains unconfirmed.
    assert section["identity"] == "UNKNOWN"
    reason = (
        "EXACT_INSTRUMENT_NOT_UNIQUE" if len(instruments) == 2 else "EXACT_INSTRUMENT_NOT_FOUND"
    )
    assert reason in saved["completeness"]["reasons"]
    assert saved["reconciliation"]["status"] == "UNRECONCILED"
    assert saved["execution"] == {"order_sending": False, "cancel_sending": False}


@pytest.mark.parametrize(
    "callback", ["OnRspQryInstrumentMarginRate", "OnRspQryInstrumentCommissionRate"]
)
def test_rates_for_another_instrument_are_never_selected_as_effective_terms(
    postgres_engine: Engine,
    clean_database: None,
    callback: str,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    batch_id = UUID(str(_begin(records)["batch_id"]))
    capture = _capture()
    events = tuple(
        replace(event, data={**event.data, "InstrumentID": "rb2610C3500"})
        if event.callback == callback and event.data
        else event
        for event in capture.events
    )
    saved = records.finish(batch_id, replace(capture, events=events))
    assert saved["status"] == "FAILED"
    assert "INSTRUMENT_QUERY_IDENTITY_MISMATCH" in saved["completeness"]["reasons"]
    assert saved["reconciliation"]["status"] == "UNRECONCILED"


@pytest.mark.parametrize(
    "failure",
    [
        "missing_last",
        "wrong_request",
        "wrong_account",
        "wrong_environment",
        "disconnected",
        "sdk_error",
    ],
)
def test_incomplete_wrong_identity_and_disconnected_queries_remain_unreconciled(
    postgres_engine: Engine,
    clean_database: None,
    failure: str,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    pending = _begin(records)
    capture = _capture(
        account="654321" if failure == "wrong_account" else "123456",
        profile="simnow_trading" if failure == "wrong_environment" else "simnow_dev",
    )
    events = list(capture.events)
    if failure == "missing_last":
        events = [
            replace(event, is_last=False) if event.callback == "OnRspQryTrade" else event
            for event in events
        ]
    elif failure == "wrong_request":
        events = [
            replace(event, request_id=999) if event.callback == "OnRspQryTrade" else event
            for event in events
        ]
    elif failure == "sdk_error":
        events = [
            replace(event, error_id=42) if event.callback == "OnRspQryTrade" else event
            for event in events
        ]
    elif failure == "disconnected":
        events.append(
            BrokerEvent(
                len(events) + 1,
                "TD",
                "OnFrontDisconnected",
                None,
                None,
                capture.finished_at,
                0,
                {"Reason": 4097},
            )
        )
    saved = records.finish(UUID(str(pending["batch_id"])), replace(capture, events=tuple(events)))
    assert saved["status"] == (
        "INCOMPLETE" if failure in {"missing_last", "wrong_request"} else "FAILED"
    )
    assert saved["reconciliation"]["status"] == "UNRECONCILED"
    assert saved["execution"] == {"order_sending": False, "cancel_sending": False}
    assert saved["completeness"]["reasons"]


def test_missing_credentials_records_no_fabricated_callback_or_zero_account(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    pending = _begin(records)
    at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    capture = QueryCapture(at, at, None, None, None, None, (), "MISSING_CREDENTIALS")
    saved = records.finish(UUID(str(pending["batch_id"])), capture)
    assert saved["status"] == "FAILED"
    assert saved["capture"]["events"] == []
    assert saved["completeness"]["trading_day"] is None
    assert saved["completeness"]["sections"]["account"]["rows"] is None
    assert saved["reconciliation"]["differences"] is None


def test_completion_is_atomic_idempotent_and_detects_storage_drift(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    batch_id = UUID(str(_begin(records)["batch_id"]))
    capture = _capture()
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(lambda _: records.finish(batch_id, capture), range(2)))
    assert outcomes[0] == outcomes[1]
    with pytest.raises(ValueError, match="conflicts"):
        records.finish(batch_id, replace(capture, failure_code="QUERY_TIMEOUT"))
    with pytest.raises(DBAPIError, match="immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(text("UPDATE broker_query_batches SET status = 'FAILED'"))
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                "UPDATE broker_query_batches SET result = jsonb_set(result, "
                "'{execution,order_sending}', 'true'::jsonb) WHERE batch_id = :id"
            ),
            {"id": batch_id},
        )
    with pytest.raises(ValueError, match="evidence"):
        records.get(batch_id)
