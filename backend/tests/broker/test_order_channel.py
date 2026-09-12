"""Native order IPC uses scripted SDK objects, no network or real account."""

import json
import multiprocessing
import time
from datetime import UTC, datetime, timedelta
from queue import Queue
from types import SimpleNamespace

import pytest

from northstar_quant.broker import _ctp_worker, ctp
from northstar_quant.broker.order_channel import OrderChannel, drain_order
from northstar_quant.broker.settings import get_profile
from tests.broker.test_ctp import _available, _credentials, _install_scripted, _Trader


class OrderTrader(_Trader):
    def ReqOrderInsert(self, native, request):
        if native.InstrumentID == "hg2610":
            time.sleep(20)
        self.OnRtnOrder(SimpleNamespace(**vars(native), OrderStatus="3"))
        return 0

    def ReqOrderAction(self, native, request):
        self.OnRspOrderAction(native, SimpleNamespace(ErrorID=31), request, True)
        return 0


def scripted_orders(
    connection,
    profile,
    credentials,
    instrument,
    directory,
    duration,
    stop_signal,
    order_queues=None,
):
    _install_scripted(instrument, streaming=True)
    _ctp_worker.importlib.import_module("ctpwrapper").TraderApiPy = OrderTrader
    structures = _ctp_worker.importlib.import_module("ctpwrapper.ApiStructure")
    structures.InputOrderField = structures.InputOrderActionField = SimpleNamespace
    _ctp_worker._capture(
        connection,
        profile,
        credentials,
        instrument,
        directory,
        duration,
        streaming=True,
        stop_signal=stop_signal,
        order_queues=order_queues,
    )


def fields(instrument="rb2610", request_id=100001):
    return dict(
        BrokerID="9999",
        InvestorID="123456",
        RequestID=request_id,
        InstrumentID=instrument,
        LimitPrice="100",
        OrderRef="501",
    )


def test_actual_spawned_channel_retains_order_and_cancel_rejection(monkeypatch):
    _available(monkeypatch, None)
    monkeypatch.setattr(_ctp_worker, "stream", scripted_orders)
    ports, events, codes = [], [], []

    def accept(event):
        events.append(event)
        if event.callback == "OnRspSubMarketData":
            codes.append(
                ports[0].send(
                    "ReqOrderInsert", fields(), 100001, datetime.now(UTC) + timedelta(seconds=2)
                )
            )
        if codes == [0] and ports[0].pending is None:
            codes.append(
                ports[0].send(
                    "ReqOrderAction",
                    fields(request_id=100002),
                    100002,
                    datetime.now(UTC) + timedelta(seconds=2),
                )
            )

    failure = ctp.stream_account(
        get_profile("simnow_dev"),
        _credentials(),
        "rb2610",
        on_event=accept,
        should_stop=lambda: any(e.callback == "OnRspOrderAction" for e in events),
        duration_seconds=10,
        on_transport=ports.append,
    )
    assert failure is None and codes == [0, 0]
    requests = [
        e
        for e in events
        if e.callback == "RequestSent" and e.request_id is not None and e.request_id > 100000
    ]
    assert [(e.request_id, e.data["return_code"]) for e in requests] == [(100001, 0), (100002, 0)]
    assert any(e.callback == "OnRspOrderAction" and e.error_id == 31 for e in events)
    assert ports[0].closed
    assert not [p for p in multiprocessing.active_children() if p.name == "northstar-ctp-stream"]


def test_hung_native_attempt_seals_channel_and_reaps_child(monkeypatch):
    _available(monkeypatch, None)
    monkeypatch.setattr(_ctp_worker, "stream", scripted_orders)
    ports, codes = [], []

    def accept(event):
        if event.callback == "OnRspSubMarketData":
            codes.append(
                ports[0].send(
                    "ReqOrderInsert",
                    fields("hg2610"),
                    100001,
                    datetime.now(UTC) + timedelta(milliseconds=150),
                )
            )

    failure = ctp.stream_account(
        get_profile("simnow_dev"),
        _credentials(),
        "hg2610",
        on_event=accept,
        should_stop=lambda: False,
        duration_seconds=10,
        on_transport=ports.append,
    )
    assert failure == "ORDER_TRANSPORT_UNKNOWN" and codes == [0]
    assert ports[0].failed and ports[0].closed
    assert not [p for p in multiprocessing.active_children() if p.name == "northstar-ctp-stream"]


@pytest.mark.parametrize("change", ["expired", "account", "repeat"])
def test_native_gate_rejects_expired_wrong_account_or_repeated_attempt(change):
    requests, replies, seen, calls = Queue(), Queue(), set(), []
    message = dict(
        method="ReqOrderInsert",
        fields=fields(),
        request_id=100001,
        expires_at=(datetime.now(UTC) + timedelta(seconds=2)).isoformat(),
    )
    if change == "expired":
        message["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif change == "account":
        message["fields"]["InvestorID"] = "654321"
    else:
        seen.add(100001)
    requests.put(json.dumps(message))
    with pytest.raises(ValueError):
        drain_order(
            requests,
            replies,
            trader=SimpleNamespace(ReqOrderInsert=lambda *a: calls.append(a)),
            structures=SimpleNamespace(InputOrderField=SimpleNamespace),
            broker_id="9999",
            account_id="123456",
            seen=seen,
            record=lambda *a: {},
        )
    assert not calls and replies.empty()


def test_parent_timeout_never_requeues_attempt():
    requests, replies = Queue(maxsize=1), Queue(maxsize=1)
    channel = OrderChannel(requests, replies)
    channel.send("ReqOrderInsert", fields(), 100001, datetime.now(UTC) + timedelta(milliseconds=10))
    time.sleep(0.02)
    channel.poll()
    with pytest.raises(ValueError, match="unavailable"):
        channel.send("ReqOrderInsert", fields(), 100001, datetime.now(UTC) + timedelta(seconds=1))
    assert requests.qsize() == 1
