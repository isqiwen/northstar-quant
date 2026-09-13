"""Owned native channel reductions with synthetic CTP facts, never external orders."""

import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.events import BrokerEvent, QueryCapture
from northstar_quant.broker.records import BrokerRecords
from tests.live.test_opening_execution import Clock, fill_initial_order, tick
from tests.live.test_opening_execution import opening_context as opening_context


@pytest.mark.parametrize(
    "condition",
    [
        None,
        "stale",
        "missing_position",
        "missing_trades",
        "revoked",
        "bad_price",
        "wrong_opening",
        "fault_then_pause",
    ],
)
def test_confirmed_opening_can_close_while_operator_paused(
    live_engine, opening_context, monkeypatch, condition
):
    from northstar_quant.execution.journal import OrderJournal
    from tests.accounting.test_ledger import position

    client, stream, budget, _, calls = opening_context
    opening_id, wire, fill = fill_initial_order(live_engine, opening_context, monkeypatch)
    Clock.at += timedelta(milliseconds=1)
    query_id = uuid4()
    sequence = client.streams.get(stream)["received"]

    def accept(callback, data, *, request_id=None, is_last=None):
        nonlocal sequence
        sequence += 1
        calls["accept"](
            BrokerEvent(
                sequence,
                "MD" if callback == "OnRtnDepthMarketData" else "TD",
                callback,
                request_id,
                is_last,
                Clock.at.isoformat().replace("+00:00", "Z"),
                0,
                data,
            )
        )

    accept("AccountQueryStarted", {"query_id": str(query_id)})
    source_id = UUID(client.streams.get(stream)["binding"]["request"]["query_batch_id"])
    original = QueryCapture.from_dict(BrokerRecords(live_engine).get(source_id)["capture"])
    for event in original.events:
        if event.callback.startswith("OnRspQry") or (
            event.callback == "RequestSent"
            and str((event.data or {}).get("method", "")).startswith("ReqQry")
        ):
            data = event.data
            if event.callback == "OnRspQryTrade":
                data = None if condition == "missing_trades" else fill.data
            elif event.callback == "OnRspQryInvestorPosition":
                data = None if condition == "missing_position" else position(quantity=1)
            elif event.callback == "OnRspQryOrder":
                data = {
                    **wire["fields"],
                    "FrontID": 7,
                    "SessionID": 99,
                    "TradingDay": "20260907",
                    "OrderSysID": "sys1",
                    "OrderStatus": "0",
                    "OrderSubmitStatus": "3",
                    "VolumeTraded": 1,
                    "VolumeTotal": 0,
                }
            elif event.callback == "OnRspQryInstrumentCommissionRate":
                data = {**data, "CloseTodayRatioByMoney": "0.0001", "CloseTodayRatioByVolume": "0"}
            accept(event.callback, data, request_id=event.request_id + 2000, is_last=event.is_last)
    accept(
        "AccountQueryFinished", {"query_id": str(query_id), "status": "COMPLETE", "reason": None}
    )
    event = tick(1, Clock.at, price="3110", volume=310)
    accept(
        "OnRtnDepthMarketData",
        {
            **event.data,
            "UpperLimitPrice": "3500",
            "LowerLimitPrice": "2800",
            "PreSettlementPrice": "3200",
        },
    )
    assert client.streams.account_query(stream, query_id)["status"] == "COMPLETE"
    if condition == "fault_then_pause":
        sequence += 1
        calls["accept"](
            BrokerEvent(
                sequence,
                "TD",
                "OnRspOrderInsert",
                199999,
                True,
                Clock.at.isoformat().replace("+00:00", "Z"),
                31,
                {**wire["fields"], "RequestID": 199999},
            )
        )
    client.streams.control(stream, "PAUSE", request_id=uuid4())
    if condition == "fault_then_pause":
        assert client.streams.get(stream)["reason"] == "ORDER_REJECTION_UNMATCHED"
    consent = uuid4()
    client.mutate(
        "/execution/authorizations",
        dict(
            stream_id=str(stream),
            expires_at=(Clock.at + timedelta(seconds=30)).isoformat(),
            max_order_lots=1,
            max_total_lots=1,
            max_order_budget=dict(fee="10", margin="0", gross="0", loss="0"),
        ),
        consent,
    )
    if condition == "stale":
        Clock.at += timedelta(seconds=6)
    if condition == "revoked":
        client.mutate(
            f"/execution/authorizations/{consent}/revoke",
            {"authorization_id": str(consent)},
            uuid4(),
        )
    if condition == "wrong_opening":
        opening_id = uuid4()
    identifier = uuid4()
    result = client.streams.submit_closing(
        stream,
        opening_id,
        query_id,
        consent,
        Decimal("2799" if condition == "bad_price" else "3110"),
        request_id=identifier,
    )
    if condition is not None:
        assert result["status"] == "REJECTED", result
        assert calls["native_orders"].empty()
        assert client.streams.get(stream)["connection"] == "RECEIVING"
        return
    assert result["status"] == "UNKNOWN", result
    assert (
        client.streams.submit_closing(
            stream, opening_id, query_id, consent, Decimal("3110"), request_id=identifier
        )
        == result
    )
    close = json.loads(calls["native_orders"].get(timeout=1))
    assert close["fields"]["CombOffsetFlag"] == "3"
    assert close["fields"]["Direction"] == "1" and close["fields"]["VolumeTotalOriginal"] == 1
    held = OrderJournal(live_engine, UUID(client.status()["runtime_id"])).get(str(identifier))[
        "reservation"
    ]
    assert held["reserved_close_lots"] == 1 and held["reserved_margin"] == "0"
    calls["native_returned"].clear()
    calls["native_returns"].put((close["request_id"], 0))
    assert calls["native_returned"].wait(2)
    Clock.at += timedelta(milliseconds=1)
    accept(
        "OnRtnOrder",
        {
            **close["fields"],
            "FrontID": 7,
            "SessionID": 99,
            "TradingDay": "20260907",
            "OrderSysID": "sys2",
            "OrderStatus": "0",
            "OrderSubmitStatus": "3",
            "VolumeTraded": 1,
            "VolumeTotal": 0,
        },
    )
    Clock.at += timedelta(milliseconds=1)
    from tests.accounting.test_ledger import trade

    accept(
        "OnRtnTrade",
        trade(
            "close1",
            Price="3112",
            Volume=1,
            TradeTime="09:03:01",
            Direction="1",
            OffsetFlag="3",
            OrderSysID="sys2",
        ),
    )
    from northstar_quant.accounting.journal import snapshot
    from northstar_quant.live.opening_execution import verify_admissions

    progress = BrokerLedger(live_engine).stream_progress(stream)
    with live_engine.connect() as connection:
        account = snapshot(connection, progress["baseline_id"]).account
        assert all(
            not any(account.position(m.contract_id).to_dict().values()) for m in account.markets
        )
        assert account.realized_pnl == Decimal("20")
        assert len(account.pending_fee_fill_ids) == 2
        with pytest.raises(ValueError, match="confirmed fees"):
            _ = account.cash
    journal = OrderJournal(live_engine, UUID(client.status()["runtime_id"]))
    assert journal.get(str(identifier))["status"] == "FILLED"
    assert journal.get(str(identifier))["reservation"]["reserved_close_lots"] == 0
    assert journal.verify_all() == 2
    assert verify_admissions(live_engine, calls["library"]) == 2


def test_closing_protocol_rejects_injected_inventory_and_invalid_price(
    live_engine, live_web_app, opening_context
):
    from tests.apps.browser import ProtocolClient, login_response

    client, stream, budget, consent, calls = opening_context
    with ProtocolClient(
        live_web_app(live_engine, calls["library"]), base_url="http://127.0.0.1"
    ) as browser:
        login = login_response(browser)
        headers = {
            "X-Northstar-CSRF": login.json()["csrf"],
            "X-Live-Runtime-ID": client.status()["runtime_id"],
        }
        body = dict(
            opening_order_id=str(uuid4()),
            query_id=budget["query_id"],
            authorization_id=str(consent),
            limit_price="3110",
            request_id=str(uuid4()),
        )
        url = f"/api/streams/{stream}/closing-orders"
        assert browser.post(url, json=body).status_code in {403, 422}
        assert (
            browser.post(url, json={**body, "position_lots": 1}, headers=headers).status_code == 422
        )
        for price in ("NaN", "-1", "not-a-price"):
            assert (
                browser.post(url, json={**body, "limit_price": price}, headers=headers).status_code
                == 422
            )
        response = browser.post(url, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "REJECTED"
        assert browser.post(url, json=body, headers=headers).json() == response.json()
        assert calls["native_orders"].empty()
