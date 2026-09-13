"""Owned receiver admission and native IPC with synthetic CTP facts only."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.events import BrokerEvent, QueryCapture
from northstar_quant.broker.order_transport import CtpExecution
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.live.opening_budgets import BrokerOpeningBudgets
from tests.broker.test_records import _capture
from tests.live.test_budgets import AccountClock, budget_query
from tests.live.test_market import OPEN, tick
from tests.live.test_streams import Clock, prepare, start


class _ClockType(type):
    def __instancecheck__(cls, value):
        return isinstance(value, datetime)


class ReceiverClock(datetime, metaclass=_ClockType):
    @classmethod
    def now(cls, tz=None):
        return Clock.at if tz is not None else Clock.at.replace(tzinfo=None)


@pytest.fixture
def opening_context(live_engine, live_client, tmp_path, monkeypatch):
    AccountClock.at = OPEN - timedelta(minutes=10)
    for module in ("broker.records", "accounting.baselines", "accounting.ledger"):
        monkeypatch.setattr(f"northstar_quant.{module}.datetime", AccountClock)
    for module in (
        "tests.broker.test_records",
        "tests.accounting.test_baselines",
        "tests.accounting.test_ledger",
        "tests.live.test_budgets",
    ):
        monkeypatch.setattr(f"{module}.datetime", AccountClock)

    def scoped_capture(**kwargs):
        capture = _capture(**kwargs)
        return replace(
            capture,
            events=tuple(
                replace(e, data={**e.data, "BizType": "1", "SettlementID": 1})
                if e.callback == "OnRspQryTradingAccount"
                else e
                for e in capture.events
            ),
        )

    monkeypatch.setattr("tests.accounting.test_baselines._capture", scoped_capture)
    library, first, config, calls = prepare(live_engine, tmp_path, monkeypatch)
    source = budget_query(live_engine)
    calls["order_transport"] = True
    client = live_client(live_engine, library).for_operator("owner")
    stream_id = uuid4()
    monkeypatch.setattr("northstar_quant.live.storage.datetime", Clock)
    start(client.streams, source, config, stream_id)
    assert calls["ready"].wait(3)
    for module in (
        "live.opening_execution",
        "live.opening_budgets",
        "live.execution_authority",
        "live.order_control",
        "broker.order_transport",
        "broker.order_channel",
        "execution.journal",
    ):
        monkeypatch.setattr(f"northstar_quant.{module}.datetime", ReceiverClock)
    events = QueryCapture.from_dict(BrokerRecords(live_engine).get(source)["capture"]).events
    sequence = 0

    def accept(event):
        nonlocal sequence
        sequence += 1
        calls["accept"](
            replace(
                event, sequence=sequence, received_at=Clock.at.isoformat().replace("+00:00", "Z")
            )
        )

    for event in events[:-1]:
        if event.channel == "TD" and event.callback == "OnRspUserLogin":
            event = replace(
                event, data={**event.data, "FrontID": 7, "SessionID": 99, "MaxOrderRef": "500"}
            )
        accept(event)
    baseline = UUID(BrokerBaselines(live_engine).context(first)["baseline"]["baseline_id"])
    Clock.at += timedelta(milliseconds=1)
    monkeypatch.setattr("northstar_quant.accounting.ledger.datetime", Clock)
    entry = BrokerLedger(live_engine).ingest_stream(
        baseline, stream_id, sequence, request_id=uuid4()
    )
    assert entry["monetary_status"] == "POSTED", entry
    for seconds in range(0, 180, 4):
        Clock.at = OPEN + timedelta(seconds=seconds)
        event = tick(1, Clock.at, price="3100" if seconds < 120 else "3110", volume=100 + seconds)
        accept(
            replace(
                event,
                data={
                    **event.data,
                    "UpperLimitPrice": "3500",
                    "LowerLimitPrice": "2800",
                    "PreSettlementPrice": "3200",
                },
            )
        )
    query_id = uuid4()
    accept(
        BrokerEvent(
            1,
            "TD",
            "AccountQueryStarted",
            None,
            None,
            Clock.at.isoformat().replace("+00:00", "Z"),
            0,
            {"query_id": str(query_id)},
        )
    )
    for event in events:
        if event.callback.startswith("OnRspQry") or (
            event.callback == "RequestSent"
            and str((event.data or {}).get("method", "")).startswith("ReqQry")
        ):
            accept(replace(event, request_id=event.request_id + 1000))
    accept(
        BrokerEvent(
            1,
            "TD",
            "AccountQueryFinished",
            None,
            None,
            Clock.at.isoformat().replace("+00:00", "Z"),
            0,
            {"query_id": str(query_id), "status": "COMPLETE", "reason": None},
        )
    )
    Clock.at = OPEN + timedelta(seconds=180)
    event = tick(1, Clock.at, price="3110", volume=300)
    accept(
        replace(
            event,
            data={
                **event.data,
                "UpperLimitPrice": "3500",
                "LowerLimitPrice": "2800",
                "PreSettlementPrice": "3200",
            },
        )
    )
    budget = BrokerOpeningBudgets(live_engine, library).create(
        stream_id,
        sequence,
        query_id,
        UUID(entry["entry_id"]),
        limit_price=Decimal("3110"),
        request_id=uuid4(),
    )
    assert budget["status"] == "WITHIN_BUDGET", budget
    assert budget["account_check"]["status"] == "UNCHANGED", budget
    consent = uuid4()
    client.mutate(
        "/execution/authorizations",
        dict(
            stream_id=str(stream_id),
            expires_at=(Clock.at + timedelta(seconds=30)).isoformat(),
            max_order_lots=1,
            max_total_lots=1,
            max_order_budget=dict(fee="10", margin="10000", gross="40000", loss="10"),
        ),
        consent,
    )
    calls["library"] = library
    return client, stream_id, budget, consent, calls


def test_opening_persists_admission_before_native_send(live_engine, opening_context):
    client, stream, budget, consent, calls = opening_context
    identifier = uuid4()
    result = client.streams.submit_opening(
        stream, UUID(budget["budget_id"]), consent, request_id=identifier
    )
    assert result["status"] == "UNKNOWN", result
    native = json.loads(calls["native_orders"].get(timeout=1))
    assert native["method"] == "ReqOrderInsert"
    assert native["fields"]["CombOffsetFlag"] == "0"
    assert native["fields"]["VolumeTotalOriginal"] == 1
    with live_engine.connect() as connection:
        binding = CtpExecution.binding(connection, str(identifier))
    proof = binding["admission"]
    assert proof["scope"] == "INITIAL_FLAT_SANDBOX_OPENING"
    assert proof["budget_id"] == budget["budget_id"]
    assert proof["query_id"] == budget["query_id"]
    assert proof["entry_id"] == budget["entry_id"]
    assert result["reservation"]["reserved_margin"] != "0"
    assert (
        client.streams.submit_opening(
            stream, UUID(budget["budget_id"]), consent, request_id=identifier
        )
        == result
    )
    assert calls["native_orders"].empty()


@pytest.mark.parametrize(
    "condition",
    ["stale", "paused", "revoked", "unresolved", "market_changed", "resumed", "other_contract"],
)
def test_opening_refusal_does_not_send_or_stop_receiver(live_engine, opening_context, condition):
    from northstar_quant.execution.journal import OrderJournal
    from tests.execution.test_journal import request

    client, stream, budget, consent, calls = opening_context
    runtime = UUID(client.status()["runtime_id"])
    if condition == "stale":
        Clock.at += timedelta(seconds=2)
    elif condition == "paused":
        client.streams.control(stream, "PAUSE", request_id=uuid4())
    elif condition == "resumed":
        client.streams.control(stream, "RESUME", request_id=uuid4())
    elif condition == "market_changed":
        Clock.at += timedelta(milliseconds=1)
        event = tick(client.streams.get(stream)["received"] + 1, Clock.at, price="3111", volume=400)
        calls["accept"](
            replace(
                event,
                data={
                    **event.data,
                    "UpperLimitPrice": "3500",
                    "LowerLimitPrice": "2800",
                    "PreSettlementPrice": "3200",
                },
            )
        )
    elif condition == "revoked":
        client.mutate(f"/execution/authorizations/{consent}/revoke", {}, uuid4())
    else:
        pending = replace(
            request(),
            contract_id=uuid4()
            if condition == "other_contract"
            else UUID(budget["inputs"]["decision"]["binding"]["contract_id"]),
            submitted_at=Clock.at,
            expires_at=Clock.at + timedelta(seconds=10),
        )
        journal = OrderJournal(live_engine, runtime)
        journal.submit(pending, uuid4(), admit=lambda _: None, dispatch=lambda _: None)
        if condition == "other_contract":
            journal.report(
                pending.order_id, evidence_id=uuid4(), state="ACCEPTED", cumulative_lots=0
            )
    identifier = uuid4()
    result = client.streams.submit_opening(
        stream, UUID(budget["budget_id"]), consent, request_id=identifier
    )
    assert result["status"] == "REJECTED", result
    assert calls["native_orders"].empty()
    assert client.streams.get(stream)["status"] == "RECEIVING"
    with pytest.raises(LookupError):
        OrderJournal(live_engine, runtime).get(str(identifier))
    assert client.command(identifier)["status"] == "COMPLETED"


def test_opening_browser_protocol_requires_session_runtime_and_fixed_inputs(
    live_engine, live_web_app, opening_context
):
    from tests.apps.browser import ProtocolClient, login_response

    _, stream, budget, consent, calls = opening_context
    with ProtocolClient(
        live_web_app(live_engine, calls["library"]), base_url="http://127.0.0.1"
    ) as browser:
        login = login_response(browser)
        headers = {
            "X-Northstar-CSRF": login.json()["csrf"],
            "X-Live-Runtime-ID": browser.get("/api/live/status").json()["runtime_id"],
        }
        body = {
            "budget_id": budget["budget_id"],
            "authorization_id": str(consent),
            "request_id": str(uuid4()),
        }
        url = f"/api/streams/{stream}/opening-orders"
        assert browser.post(url, json=body).status_code in {403, 422}
        assert (
            browser.post(url, json={**body, "available": "99999999"}, headers=headers).status_code
            == 422
        )
        response = browser.post(url, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["request_id"] == body["request_id"]
        assert response.json()["status"] == "UNKNOWN"
        assert calls["native_orders"].qsize() == 1
        assert browser.post(url, json=body, headers=headers).json() == response.json()
        assert calls["native_orders"].qsize() == 1


def test_pause_after_commit_retains_unknown_attempt_without_native_send(
    live_engine, opening_context, monkeypatch
):
    client, stream, budget, consent, calls = opening_context
    original = CtpExecution._send

    def paused(self, *args):
        client.streams.control(stream, "PAUSE", request_id=uuid4())
        return original(self, *args)

    monkeypatch.setattr(CtpExecution, "_send", paused)
    result = client.streams.submit_opening(
        stream, UUID(budget["budget_id"]), consent, request_id=uuid4()
    )
    assert result["status"] == "UNKNOWN"
    assert result["reservation"]["reserved_margin"] != "0"
    assert calls["native_orders"].empty()
    assert client.streams.get(stream)["status"] == "RECEIVING"


def test_admitted_order_confirmed_fill_posts_to_bound_account_once(
    live_engine, opening_context, monkeypatch
):
    from northstar_quant.accounting.journal import snapshot
    from northstar_quant.broker.execution_fills import verify_all
    from northstar_quant.execution.journal import OrderJournal
    from tests.accounting.test_ledger import trade

    class CommitClock(ReceiverClock):
        @classmethod
        def now(cls, tz=None):
            return Clock.at - timedelta(microseconds=1)

    client, stream, budget, consent, calls = opening_context
    identifier = uuid4()
    client.streams.submit_opening(stream, UUID(budget["budget_id"]), consent, request_id=identifier)
    wire = json.loads(calls["native_orders"].get(timeout=1))
    calls["native_returns"].put((wire["request_id"], 0))
    assert calls["native_returned"].wait(2)
    monkeypatch.setattr("northstar_quant.live.storage.datetime", CommitClock)
    Clock.at += timedelta(seconds=1)
    sequence = client.streams.get(stream)["received"]
    calls["accept"](
        BrokerEvent(
            sequence + 1,
            "TD",
            "OnRtnOrder",
            None,
            None,
            Clock.at.isoformat().replace("+00:00", "Z"),
            0,
            {
                **wire["fields"],
                "FrontID": 7,
                "SessionID": 99,
                "TradingDay": "20260907",
                "OrderSysID": "sys1",
                "OrderStatus": "0",
                "OrderSubmitStatus": "3",
                "VolumeTraded": 1,
                "VolumeTotal": 0,
            },
        )
    )
    Clock.at += timedelta(milliseconds=1)
    fill = BrokerEvent(
        sequence + 2,
        "TD",
        "OnRtnTrade",
        None,
        None,
        Clock.at.isoformat().replace("+00:00", "Z"),
        0,
        trade("open1", Price="3110", Volume=1, TradeTime="09:03:01", OrderSysID="sys1"),
    )
    calls["accept"](fill)
    journal = OrderJournal(live_engine, UUID(client.status()["runtime_id"]))
    result = journal.get(str(identifier))
    assert result["status"] == "FILLED" and result["filled_lots"] == 1
    assert result["fee_pending_lots"] == 1
    assert result["reservation"]["reserved_margin"] == "0"
    assert result["reservation"]["reserved_fee"] != "0"
    with live_engine.connect() as connection:
        entry = BrokerLedger(live_engine).get(UUID(budget["entry_id"]))
        account = snapshot(connection, entry["baseline_id"]).account
        assert (
            account.position(
                UUID(budget["inputs"]["decision"]["binding"]["contract_id"])
            ).long_today
            == 1
        )
        assert len(account.pending_fee_fill_ids) == 1
    Clock.at += timedelta(milliseconds=1)
    calls["accept"](
        replace(
            fill, sequence=sequence + 3, received_at=Clock.at.isoformat().replace("+00:00", "Z")
        )
    )
    assert journal.get(str(identifier)) == result
    assert verify_all(live_engine) == 1
    assert journal.verify_all() == 1
    from northstar_quant.live.opening_execution import verify_admissions

    assert verify_admissions(live_engine, calls["library"]) == 1
