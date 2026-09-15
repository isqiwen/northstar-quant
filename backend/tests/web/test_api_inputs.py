"""Protocol errors cannot bypass command identity or reach the Live owner."""

from uuid import uuid4

import httpx2 as httpx
from fastapi.testclient import TestClient

from northstar_quant.apps.live import api_pb2, create_app
from northstar_quant.apps.live.instances import Instances
from northstar_quant.live.auth import LiveAuth
from northstar_quant.live.client import LiveClient
from northstar_quant.live.instances import Instance
from northstar_quant.web import auth_pb2
from northstar_quant.web.protobuf import decode
from tests.apps.browser import login_response


def test_protobuf_commands_reject_unscoped_or_malformed_requests_before_owner() -> None:
    sent = []

    def owner(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(503, json={"detail": "not connected"})

    live = LiveClient(
        "http://127.0.0.1:1",
        LiveAuth("read-only-api-test-" * 3),
        transport=httpx.MockTransport(owner),
    )
    app = create_app(instances=Instances({"sim": live}, [Instance("sim", "simnow_trading")]))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        path = f"/api/streams/{uuid4()}/control"
        valid = api_pb2.ControlRequest(action="STOP", request_id=str(uuid4())).SerializeToString()
        assert (
            client.post(
                path, content=valid, headers={"Content-Type": "application/protobuf"}
            ).status_code
            == 401
        )
        session = login_response(client)
        csrf = decode(auth_pb2.BrowserSession.DESCRIPTOR, session.content)["csrf"]
        client.headers.update(
            {
                "X-Northstar-CSRF": csrf,
                "X-Live-Runtime-Id": str(uuid4()),
                "Content-Type": "application/protobuf",
            }
        )
        for content in (
            b"",  # Required command identity and action are absent.
            b"\xff",  # Truncated wire message.
            valid + b"\xf8\x7f\x01",  # Unknown field, no compatibility fallback.
            api_pb2.ControlRequest(action="RECONNECT", request_id=str(uuid4())).SerializeToString(),
            api_pb2.ControlRequest(action="STOP", request_id="not-a-uuid").SerializeToString(),
        ):
            assert client.post(path, content=content).status_code == 422
        assert (
            client.post(path, content=valid, headers={"X-Live-Runtime-Id": "wrong"}).status_code
            == 422
        )
        assert (
            client.post(
                path, json={"action": "STOP"}, headers={"Content-Type": "application/json"}
            ).status_code
            == 415
        )
        assert client.post(path, content=b"x" * (8 * 1024 * 1024 + 1)).status_code == 413
        assert sent == []


def test_cash_flow_protocol_preserves_exact_amount_and_explicit_reversal():
    from northstar_quant.web.protobuf import pack

    original = dict(
        cash_flow_id="deposit",
        amount="1234567890.123456789012345678",
        currency="CNY",
        transferred_at="2026-09-07T01:00:01+00:00",
        available_at="2026-09-07T01:00:02+00:00",
        source_reference="stream:source:2",
        reverses_id=None,
    )
    reversal = {
        **original,
        "cash_flow_id": "reversal",
        "amount": "-1234567890.123456789012345678",
        "reverses_id": "deposit",
    }
    value = {"entry_id": "entry", "added_cash_flows": [original, reversal]}
    encoded = pack(api_pb2.PositionEntry.DESCRIPTOR, value).SerializeToString()
    assert decode(api_pb2.PositionEntry.DESCRIPTOR, encoded) == value


def test_settlement_document_wire_preserves_text_and_unknown_content():
    from northstar_quant.web.protobuf import pack

    document = dict(
        status="RECEIVED",
        trading_day="2026-09-03",
        content="手续费：12.340000000000000001\n",
        content_sha256="a" * 64,
        encoding="GBK",
        problems=[],
        ledger_posted=False,
        confirmation_sent=False,
    )
    value = dict(
        batch_id=str(uuid4()), instrument="rb2610", status="COMPLETE", settlement_statement=document
    )
    for content in (document["content"], None):
        document["content"] = content
        encoded = pack(api_pb2.QueryRecord.DESCRIPTOR, value).SerializeToString()
        assert decode(api_pb2.QueryRecord.DESCRIPTOR, encoded)["settlement_statement"] == document
