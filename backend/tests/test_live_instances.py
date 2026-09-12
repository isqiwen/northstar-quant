"""Account isolation, fixed command targets and restart ownership."""

import subprocess
import sys
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
def local_engine(tmp_path, monkeypatch):
    from northstar_quant.live import account_ownership

    directory = tmp_path / "accounts"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(account_ownership, "ACCOUNT_DIRECTORY", directory)
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


def test_account_excludes_other_database_and_process_until_owner_stops(local_engine, tmp_path):
    from sqlalchemy import text

    from northstar_quant.live.storage import initialize, open_store

    other = open_store(tmp_path / "other.sqlite")
    initialize(other)
    first = InstanceBinding(local_engine, Instance("first", "simnow_dev"), "9999", "12345")
    script = """
import sys
from pathlib import Path
from northstar_quant.live import account_ownership
from northstar_quant.live.instances import Instance, InstanceBinding
from northstar_quant.live.storage import open_store
account_ownership.ACCOUNT_DIRECTORY = Path(sys.argv[1])
engine = open_store(Path(sys.argv[2]))
try:
    owner = InstanceBinding(engine, Instance('second', 'simnow_dev'), '9999', '12345')
except ValueError as error:
    assert 'account already has an active Live instance' in str(error)
    sys.exit(2)
owner.close()
engine.dispose()
"""
    command = [
        sys.executable,
        "-c",
        script,
        str(tmp_path / "accounts"),
        str(tmp_path / "other.sqlite"),
    ]
    try:
        assert subprocess.run(command, timeout=10).returncode == 2
        with other.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM live_instance_binding")).scalar() == 0
            )
        # A different account, or the same number in a different environment,
        # is independent. None of these acquisitions authorizes a broker call.
        for environment, account in [("simnow_trading", "12345"), ("simnow_dev", "54321")]:
            from northstar_quant.live.account_ownership import AccountOwnership

            independent = AccountOwnership(environment, "9999", account)
            independent.check()
            independent.close()
    finally:
        first.close()
        other.dispose()
    assert subprocess.run(command, timeout=10).returncode == 0


def test_replaced_account_lock_and_changed_credentials_are_rejected(local_engine, tmp_path):
    owner = InstanceBinding(local_engine, Instance("sim", "simnow_dev"), "9999", "12345")
    try:
        owner.require_account("simnow_dev", "9999", "12345")
        with pytest.raises(ValueError, match="credentials differ"):
            owner.require_account("simnow_dev", "9999", "54321")
        path = next((tmp_path / "accounts").glob("*.owner"))
        path.rename(path.with_suffix(".removed"))
        path.touch(mode=0o600)
        with pytest.raises(ValueError, match="lock was replaced"):
            owner.status()
    finally:
        owner.close()


def test_account_lock_directory_must_exist_and_be_private(local_engine, tmp_path):
    directory = tmp_path / "accounts"
    directory.rmdir()
    with pytest.raises(ValueError, match="existing local directory"):
        InstanceBinding(local_engine, Instance("sim", "simnow_dev"), "9999", "12345")
    directory.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="owner-only"):
        InstanceBinding(local_engine, Instance("sim", "simnow_dev"), "9999", "12345")
    directory.chmod(0o700)
    owner = InstanceBinding(local_engine, Instance("sim", "simnow_dev"), "9999", "12345")
    owner.close()


def test_failed_receiver_shutdown_does_not_release_account(local_engine, tmp_path, monkeypatch):
    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.data_management.library import DataLibrary
    from northstar_quant.live.account_ownership import AccountOwnership
    from northstar_quant.live.owner import LiveOwner

    owner = LiveOwner(local_engine, DataLibrary(local_engine, SourceFiles(tmp_path / "source")))
    owner.binding = InstanceBinding(local_engine, Instance("sim", "simnow_dev"), "9999", "12345")

    def stalled_shutdown():
        raise RuntimeError("receiver still running")

    monkeypatch.setattr(owner.streams, "close", stalled_shutdown)
    try:
        with pytest.raises(RuntimeError, match="receiver still running"):
            owner.close()
        with pytest.raises(ValueError, match="account already has an active Live instance"):
            AccountOwnership("simnow_dev", "9999", "12345")
    finally:
        owner.binding.close()


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
            with pytest.raises(HTTPException) as denied:
                registry.for_request(request)
            assert denied.value.status_code == 401
            request.state.operator = "owner"
            bound = registry.for_request(request)
            assert bound is not clients[name]
            bound.close()  # A request must not close the application's connection pool.
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
                "x-northstar-protocol": "4",
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


def test_unconfigured_account_cannot_change_broker_profile(local_engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    from northstar_quant.persistence.sql import write_transaction

    owner = InstanceBinding(local_engine, Instance("sim", "simnow_dev"), "9999", "")
    try:
        with pytest.raises(DatabaseError, match="immutable"):
            with write_transaction(local_engine) as connection:
                connection.execute(
                    text("UPDATE live_instance_binding SET broker_profile='simnow_trading'")
                )
        assert owner.status()["broker_profile"] == "simnow_dev"
        assert owner.status()["environment"] == "SANDBOX"
    finally:
        owner.close()
