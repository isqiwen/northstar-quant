"""Operator -> same receiver core -> durable OMS -> synthetic private IPC."""

import json
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

from northstar_quant.broker.events import BrokerEvent
from northstar_quant.broker.order_transport import CtpExecution
from tests.broker.test_order_transport import instrument, session
from tests.execution.test_journal import request
from tests.live.test_market import OPEN
from tests.live.test_streams import prepare, start


def test_current_owner_cancel_runs_on_receiver_and_retains_budget_until_broker_ack(
    live_engine,
    live_client,
    tmp_path,
    monkeypatch,
):
    library, source, config, calls = prepare(live_engine, tmp_path, monkeypatch)
    calls["order_transport"] = True
    client = live_client(live_engine, library).for_operator("owner")
    stream_id = uuid4()
    stream = start(client.streams, source, config, stream_id)
    assert calls["ready"].wait(3)
    runtime = UUID(client.status()["runtime_id"])
    order = replace(request(), contract_id=UUID(stream["binding"]["contract_id"]))
    adapter = CtpExecution(live_engine, runtime, replace(session(), trading_day=OPEN.date()))
    adapter.submit(
        order,
        uuid4(),
        instrument(order),
        Decimal(100),
        admit=lambda c: None,
        send=lambda *args: 0,
        check_owner=lambda: None,
    )
    baseline = adapter.journal.get(order.order_id)["reservation"]
    events = [
        BrokerEvent(
            1,
            "TD",
            "OnRspUserLogin",
            2,
            True,
            OPEN.isoformat().replace("+00:00", "Z"),
            0,
            dict(
                UserID="123456",
                BrokerID="9999",
                TradingDay=OPEN.strftime("%Y%m%d"),
                FrontID=7,
                SessionID=99,
                MaxOrderRef="501",
            ),
        ),
        BrokerEvent(
            2,
            "MD",
            "OnRspUserLogin",
            0,
            True,
            OPEN.isoformat().replace("+00:00", "Z"),
            0,
            dict(UserID="123456", BrokerID="9999", TradingDay=OPEN.strftime("%Y%m%d")),
        ),
        BrokerEvent(
            3,
            "MD",
            "OnRspSubMarketData",
            0,
            True,
            OPEN.isoformat().replace("+00:00", "Z"),
            0,
            dict(InstrumentID="rb2610"),
        ),
    ]
    for event in events:
        calls["accept"](event)
    # Shadow starts paused and no complete fee/cash authority exists. Cancelling
    # an already owned order does not require another opening-risk permit.
    command_id = uuid4()
    result = client.mutate(
        f"/execution/orders/{order.order_id}/cancel", {"stream_id": str(stream_id)}, command_id
    )
    assert result["status"] == "ATTEMPT_RECORDED", result
    native = json.loads(calls["native_orders"].get(timeout=1))
    assert native["method"] == "ReqOrderAction"
    assert native["fields"]["OrderRef"] == "501"
    assert native["fields"]["FrontID"] == 7
    assert adapter.journal.get(order.order_id)["reservation"] == baseline
    assert adapter.journal.get(order.order_id)["status"] == "UNKNOWN"
    assert (
        client.mutate(
            f"/execution/orders/{order.order_id}/cancel", {"stream_id": str(stream_id)}, command_id
        )
        == result
    )
    assert calls["native_orders"].empty()
    # Native acceptance of the IPC attempt is separate from a canceled order.
    calls["native_returns"].put((native["request_id"], 0))
    assert calls["native_returned"].wait(2)
    second = client.mutate(
        f"/execution/orders/{order.order_id}/cancel", {"stream_id": str(stream_id)}, uuid4()
    )
    assert second["status"] == "REJECTED"
    assert second["reason"] == "CANCELLATION_UNRESOLVED"
    assert calls["native_orders"].empty()
    missing = client.mutate(
        f"/execution/orders/{uuid4()}/cancel", {"stream_id": str(stream_id)}, uuid4()
    )
    assert missing["status"] == "REJECTED" and missing["reason"] == "ORDER_NOT_OWNED"
    assert client.streams.get(stream_id)["status"] == "RECEIVING"
    assert adapter.journal.verify_all() == 1


def test_control_rejects_missing_receiver_without_creating_an_sdk_connection(
    live_engine,
    live_client,
    tmp_path,
    monkeypatch,
):
    library, _, _, calls = prepare(live_engine, tmp_path, monkeypatch)
    client = live_client(live_engine, library).for_operator("owner")
    result = client.mutate(
        f"/execution/orders/{uuid4()}/cancel", {"stream_id": str(uuid4())}, uuid4()
    )
    assert result["status"] == "REJECTED"
    assert result["reason"] == "RECEIVER_NOT_ATTACHED"
    assert calls["count"] == 0


def test_a_timed_out_handoff_cannot_execute_when_core_later_recovers(live_engine):
    from northstar_quant.live.order_control import ReceiverOrders

    control = ReceiverOrders(live_engine, uuid4(), uuid4(), lambda: None)
    result = control.request(str(uuid4()), uuid4())
    assert result["status"] == "REJECTED" and result["reason"] == "COMMAND_EXPIRED"
    executed = []
    control.poll(lambda command: executed.append(command) or {})
    assert executed == []
    control.close()
    assert control.request(str(uuid4()), uuid4())["reason"] == "RECEIVER_STOPPED"
