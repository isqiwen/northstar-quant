"""The receiving connection's facts stay distinct from its earlier query."""

from dataclasses import replace
from uuid import uuid4

import pytest

from northstar_quant.broker.events import BrokerEvent
from northstar_quant.broker.stream_queries import startup_query
from tests.broker.test_records import _capture
from tests.live.test_streams import prepare, start


def test_receiver_startup_query_uses_its_own_fixed_prefix_without_reconnecting(
    live_engine, live_client, tmp_path, monkeypatch
):
    library, source, config, calls = prepare(live_engine, tmp_path, monkeypatch)
    client = live_client(live_engine, library).for_operator("owner")
    identifier = uuid4()
    initial = start(client.streams, source, config, identifier)
    assert initial["startup_query"]["status"] == "PENDING"
    assert calls["ready"].wait(3)
    events = _capture().events
    for event in events:
        if event.callback == "OnRspQryTradingAccount":
            event = replace(event, data={**event.data, "Balance": "123000"})
        calls["accept"](event)
    saved = client.streams.get(identifier)["startup_query"]
    assert saved["status"] == "COMPLETE"
    assert saved["completeness"]["identity"] == "CONFIRMED"
    assert saved["completeness"]["sections"]["account"]["rows"][0]["Balance"] == "123000"
    assert saved["execution"]["order_sending"] is False
    assert saved["reconciliation"]["status"] == "UNRECONCILED"
    assert saved["through_sequence"] == len(events) - 1
    calls["accept"](
        BrokerEvent(
            len(events) + 1,
            "TD",
            "OnFrontDisconnected",
            None,
            None,
            events[-1].received_at,
            0,
            {"Reason": 4097},
        )
    )
    assert client.streams.get(identifier)["startup_query"] == saved
    assert startup_query(live_engine, identifier) == saved
    assert calls["count"] == 1
    # A damaged callback must not be replaced with the earlier query's balance.
    client.streams.control(identifier, "STOP", request_id=uuid4())
    with live_engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER immutable_broker_stream_events_UPDATE")
        connection.exec_driver_sql(
            "UPDATE broker_stream_events SET event_hash=? WHERE stream_id=? AND sequence=1",
            ("0" * 64, identifier.hex),
        )
    with pytest.raises(ValueError, match="source is missing or damaged"):
        startup_query(live_engine, identifier)


def test_receiver_incomplete_and_failed_query_cannot_inherit_prior_success(
    live_engine, live_client, tmp_path, monkeypatch
):
    library, source, config, calls = prepare(live_engine, tmp_path, monkeypatch)
    client = live_client(live_engine, library).for_operator("owner")
    identifier = uuid4()
    start(client.streams, source, config, identifier)
    assert calls["ready"].wait(3)
    events = _capture().events
    for event in events:
        if event.callback == "OnRspQryTradingAccount":
            break
        calls["accept"](event)
    pending = startup_query(live_engine, identifier)
    assert pending["status"] == "INCOMPLETE"
    assert pending["completeness"]["sections"]["account"]["rows"] == []
    calls["accept"](replace(event, error_id=3, data=None))
    failed = client.streams.get(identifier)["startup_query"]
    assert failed["status"] == "FAILED"
    assert failed["completeness"]["sections"]["account"]["status"] == "ERROR"
    assert failed["execution"]["order_sending"] is False
    client.streams.control(identifier, "STOP", request_id=uuid4())
    assert startup_query(live_engine, identifier) == failed
    assert calls["count"] == 1


@pytest.mark.parametrize("regressed", [False, True])
def test_receiver_refresh_preserves_session_and_fixed_window(
    live_engine, live_client, tmp_path, monkeypatch, regressed
):
    from northstar_quant.broker.query_window import receiver_query

    library, source, config, calls = prepare(live_engine, tmp_path, monkeypatch)
    client = live_client(live_engine, library).for_operator("owner")
    identifier, query_id = uuid4(), uuid4()
    start(client.streams, source, config, identifier)
    assert calls["ready"].wait(3)
    events = _capture().events
    for event in events:
        calls["accept"](event)
    assert receiver_query(live_engine, identifier) is None
    sequence = len(events)

    def marker(callback, data):
        nonlocal sequence
        sequence += 1
        calls["accept"](
            BrokerEvent(sequence, "TD", callback, None, None, events[-1].received_at, 0, data)
        )

    marker("AccountQueryStarted", {"query_id": str(query_id)})
    assert receiver_query(live_engine, identifier)["status"] == "INCOMPLETE"
    for event in events:
        if event.callback.startswith("OnRspQry") or (
            event.callback == "RequestSent"
            and str((event.data or {}).get("method", "")).startswith("ReqQry")
        ):
            sequence += 1
            data = event.data
            if event.callback == "OnRspQryTradingAccount":
                data = {**data, "Balance": "456789"}
            calls["accept"](
                replace(
                    event,
                    sequence=sequence,
                    request_id=event.request_id + 1000,
                    data=data,
                    received_at=event.received_at if regressed else events[-1].received_at,
                )
            )
    marker(
        "AccountQueryFinished", {"query_id": str(query_id), "status": "COMPLETE", "reason": None}
    )
    fixed = receiver_query(live_engine, identifier)
    if regressed:
        assert fixed["status"] == "FAILED"
        assert "QUERY_RECEIPT_TIME_REGRESSED" in fixed["completeness"]["reasons"]
        client.streams.control(identifier, "STOP", request_id=uuid4())
        return
    assert fixed["status"] == "COMPLETE", fixed
    assert fixed["query_id"] == str(query_id)
    observation = fixed["account_observation"]
    assert observation["source_stream_id"] == str(identifier)
    assert "source_batch_id" not in observation
    assert observation["amounts"]["Balance"] == "456789"
    assert observation["account_receipts"][0]["sequence"] >= fixed["from_sequence"]

    assert fixed["completeness"]["sections"]["account"]["rows"][0]["Balance"] == "456789"
    assert fixed["reconciliation"]["status"] == "UNRECONCILED"
    assert fixed["execution"]["order_sending"] is False
    marker("OnFrontDisconnected", {"Reason": 4097})
    assert receiver_query(live_engine, identifier) == fixed
    marker("AccountQueryStarted", {"query_id": str(uuid4())})
    assert receiver_query(live_engine, identifier)["status"] == "FAILED"
    assert calls["count"] == 1
    client.streams.control(identifier, "STOP", request_id=uuid4())
    assert receiver_query(live_engine, identifier)["status"] == "FAILED"
    assert receiver_query(live_engine, identifier, query_id=query_id) == fixed
    assert client.streams.account_query(identifier, query_id)["source_hash"] == fixed["source_hash"]
    with pytest.raises(LookupError, match="not found"):
        client.streams.account_query(identifier, uuid4())
    with live_engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER immutable_broker_stream_events_UPDATE")
        connection.exec_driver_sql(
            "UPDATE broker_stream_events SET event_hash=? WHERE stream_id=? AND sequence=?",
            ("0" * 64, identifier.hex, sequence),
        )
    with pytest.raises(ValueError, match="source is missing or damaged"):
        receiver_query(live_engine, identifier)
