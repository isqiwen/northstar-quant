"""Account isolation, fixed command targets and restart ownership."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx2 as httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from northstar_quant.apps.live.instances import Instances
from northstar_quant.live.auth import LiveAuth
from northstar_quant.live.client import LiveClient, RuntimeUnavailable
from northstar_quant.live.instances import Instance, InstanceBinding, configured_instances


@pytest.fixture
def local_engine(tmp_path):
    from northstar_quant.live.storage import initialize, open_store

    engine = open_store(tmp_path / "live.sqlite")
    initialize(engine)
    yield engine
    engine.dispose()


def test_account_binding_survives_restart_and_refuses_retarget(local_engine):
    original = Instance("stable", "simnow_trading")
    first = InstanceBinding(local_engine, original, "9999", "12345")
    try:
        with pytest.raises(ValueError, match="active kernel"):
            InstanceBinding(local_engine, original, "9999", "12345")
    finally:
        first.close()
    for instance, account in [
        (original, "45678"),
        (Instance("stable", "simnow_dev"), "12345"),
        (Instance("other", "simnow_trading"), "12345"),
    ]:
        with pytest.raises(ValueError, match="binding differs"):
            InstanceBinding(local_engine, instance, "9999", account)
    reopened = InstanceBinding(local_engine, original, "9999", "12345")
    assert reopened.status()["instance_id"] == "stable"
    reopened.close()


def test_unconfigured_account_can_be_bound_once(local_engine):
    instance = Instance("sim", "simnow_dev")
    blank = InstanceBinding(local_engine, instance, "9999", "")
    blank.close()
    bound = InstanceBinding(local_engine, instance, "9999", "12345")
    bound.close()
    with pytest.raises(ValueError, match="binding differs"):
        InstanceBinding(local_engine, instance, "9999", "")


def test_configuration_refuses_duplicate_account_and_production():
    assert len(configured_instances("sim:simnow_trading,dev:simnow_dev")) == 2
    for config in ["a:simnow_dev,b:simnow_dev", "a:production", "a:simnow_dev,a:simnow_trading"]:
        with pytest.raises(ValueError):
            configured_instances(config)


def test_management_routes_explicitly_and_never_falls_back():
    clients = {
        name: LiveClient("http://kernel", LiveAuth("r" * 32, "c" * 32)) for name in ("a", "b")
    }
    registry = Instances(clients, [Instance("a", "simnow_trading"), Instance("b", "simnow_dev")])
    try:
        for name in clients:
            request = Request({"type": "http", "headers": [(b"x-live-instance-id", name.encode())]})
            assert registry.for_request(request) is clients[name]
        for name in (b"missing", b"http://other"):
            with pytest.raises(HTTPException):
                registry.for_request(
                    Request({"type": "http", "headers": [(b"x-live-instance-id", name)]})
                )
        with pytest.raises(HTTPException):
            registry.for_request(Request({"type": "http", "headers": []}))
    finally:
        registry.close()


def test_wrong_endpoint_identity_cannot_receive_a_command():
    requests = []

    def owner(request):
        requests.append(request.method)
        return httpx.Response(
            200,
            json={},
            headers={
                "x-northstar-protocol": "2",
                "x-live-instance-id": "wrong",
                "x-live-runtime-id": str(uuid4()),
                "x-live-observed-at": datetime.now(UTC).isoformat(),
            },
        )

    client = LiveClient(
        "http://kernel",
        LiveAuth("r" * 32, "c" * 32),
        transport=httpx.MockTransport(owner),
        expected_instance_id="sim",
    )
    try:
        with pytest.raises(RuntimeUnavailable, match="different instance"):
            client.mutate("/queries", {}, uuid4())
        assert requests == ["GET"]
    finally:
        client.close()
