"""A counter operation refusal is not a disconnected account or a filled order."""

import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.events import BrokerEvent
from northstar_quant.broker.execution_reports import verify_all
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.execution.journal import OrderJournal
from tests.accounting.test_ledger import trade
from tests.broker.test_records import _begin, _capture
from tests.live.test_opening_execution import ReceiverClock
from tests.live.test_opening_execution import opening_context as opening_context
from tests.live.test_streams import Clock


@pytest.mark.parametrize(
    "callback",
    ["OnRspOrderInsert", "OnErrRtnOrderInsert", "OnRspOrderAction", "OnErrRtnOrderAction"],
)
@pytest.mark.parametrize("matched", [True, False])
def test_rejected_opening_preserves_account_and_receiver_or_flags_unknown_attempt(
    live_engine, opening_context, monkeypatch, callback, matched
):
    client, stream, budget, consent, calls = opening_context
    identifier = uuid4()
    client.streams.submit_opening(stream, UUID(budget["budget_id"]), consent, request_id=identifier)
    wire = json.loads(calls["native_orders"].get(timeout=1))
    opening_wire = wire
    calls["native_returns"].put((wire["request_id"], 0))
    assert calls["native_returned"].wait(2)
    cancel = callback.endswith("OrderAction")
    if cancel:
        client.mutate(f"/execution/orders/{identifier}/cancel", {"stream_id": str(stream)}, uuid4())
        wire = json.loads(calls["native_orders"].get(timeout=1))
        assert wire["method"] == "ReqOrderAction"
        calls["native_returned"].clear()
        calls["native_returns"].put((wire["request_id"], 0))
        assert calls["native_returned"].wait(2)
    Clock.at += timedelta(milliseconds=1)
    request = wire["request_id"] + (0 if matched else 10)
    calls["accept"](
        BrokerEvent(
            client.streams.get(stream)["received"] + 1,
            "TD",
            callback,
            request if callback.startswith("OnRsp") else None,
            True if callback.startswith("OnRsp") else None,
            Clock.at.isoformat().replace("+00:00", "Z"),
            31,
            {**wire["fields"], "RequestID": request},
        )
    )
    journal = OrderJournal(live_engine, uuid4())
    order = journal.get(str(identifier))
    current = client.streams.get(stream)
    assert current["status"] == "RECEIVING"
    assert current["paused"] == (not matched)
    if matched:
        assert order["status"] == ("UNKNOWN" if cancel else "REJECTED")
        assert (order["reservation"]["reserved_margin"] != "0") == cancel
        assert BrokerLedger(live_engine).stream_progress(stream)["status"] == "READY"
        BrokerLedger(live_engine).verify_all()
        assert verify_all(live_engine) == 1
    else:
        assert current["reason"] == "ORDER_REJECTION_UNMATCHED"
        assert order["status"] == "UNKNOWN"
        assert order["reservation"]["reserved_margin"] != "0"
    assert journal.verify_all() == 1
    assert calls["native_orders"].empty()
    if cancel and matched:

        class CommitClock(ReceiverClock):
            @classmethod
            def now(cls, tz=None):
                return Clock.at - timedelta(microseconds=1)

        monkeypatch.setattr("northstar_quant.live.storage.datetime", CommitClock)
        Clock.at += timedelta(seconds=1)
        sequence = current["received"]
        for index, (callback_name, data) in enumerate(
            [
                (
                    "OnRtnOrder",
                    {
                        **opening_wire["fields"],
                        "FrontID": 7,
                        "SessionID": 99,
                        "TradingDay": "20260907",
                        "OrderSysID": "sys1",
                        "OrderStatus": "0",
                        "OrderSubmitStatus": "3",
                        "VolumeTraded": 1,
                        "VolumeTotal": 0,
                    },
                ),
                (
                    "OnRtnTrade",
                    trade("open1", Price="3110", Volume=1, TradeTime="09:03:01", OrderSysID="sys1"),
                ),
            ],
            start=1,
        ):
            Clock.at += timedelta(milliseconds=1)
            calls["accept"](
                BrokerEvent(
                    sequence + index,
                    "TD",
                    callback_name,
                    None,
                    None,
                    Clock.at.isoformat().replace("+00:00", "Z"),
                    0,
                    data,
                )
            )
        assert journal.get(str(identifier))["status"] == "FILLED"
        assert BrokerLedger(live_engine).stream_progress(stream)["status"] == "READY"
        BrokerLedger(live_engine).verify_all()
        assert journal.verify_all() == 1


@pytest.mark.parametrize("foreign", [False, True])
def test_query_completion_is_separate_from_scoped_order_rejection(live_engine, foreign):
    records = BrokerRecords(live_engine)
    identifier = UUID(str(_begin(records)["batch_id"]))
    capture = _capture()
    rejection = BrokerEvent(
        len(capture.events) + 1,
        "TD",
        "OnRspOrderInsert",
        100001,
        True,
        capture.finished_at,
        31,
        {"BrokerID": "9999", "InvestorID": "654321" if foreign else "123456"},
    )
    result = records.finish(identifier, replace(capture, events=(*capture.events, rejection)))
    assert result["status"] == ("FAILED" if foreign else "COMPLETE")
    assert result["execution"]["order_sending"] is False
    assert result["reconciliation"]["status"] == "UNRECONCILED"
    assert records.get(identifier) == result
