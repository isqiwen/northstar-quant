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
