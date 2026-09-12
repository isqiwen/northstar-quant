"""Independent Live HTTP ownership, permission and durable unknown-result behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from sqlalchemy import Engine

from northstar_quant.apps.live.kernel import create_app
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live import (
    CommandUnknown,
    LiveAuth,
    LiveClient,
    RuntimeUnavailable,
)
from northstar_quant.live.client import PROTOCOL_VERSION
from tests.accounting.test_baselines import saved_query
from tests.apps.browser import ProtocolClient as TestClient


def test_live_auth_expiry_and_target_rejection_have_no_account_effect(
    live_engine: Engine, tmp_path: Path
) -> None:
    source = saved_query(live_engine)
    auth = LiveAuth("r" * 48, "c" * 48)
    app = create_app(live_engine, DataLibrary(live_engine, SourceFiles(tmp_path)), auth)
    with TestClient(app) as http:
        client = LiveClient("http://localhost", auth, client=http)
        status = client.status()
        assert status["status"] == "AVAILABLE"
        assert status["order_sending"] is False and status["cancel_sending"] is False
        assert client.streams.list() == []  # Startup is not a broker query or reception request.
        path = "/broker/baselines"
        body = {"source_batch_id": str(source)}
        headers = {
            "Authorization": "Bearer " + auth.read_token,
            "X-Northstar-Protocol": PROTOCOL_VERSION,
            "X-Northstar-Operator": "maintenance",
            "X-Live-Command-ID": str(uuid4()),
            "X-Live-Runtime-ID": status["runtime_id"],
            "X-Live-Expires-At": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        }
        assert http.post(path, json=body, headers=headers).status_code == 403
        readonly = LiveClient("http://localhost", LiveAuth(auth.read_token), client=http)
        assert readonly.status()["control_available"] is False
        with pytest.raises(ValueError, match="read permission only"):
            readonly.broker.establish_baseline(source, request_id=uuid4())
        headers["Authorization"] = "Bearer " + str(auth.control_token)
        missing_operator = {
            key: value for key, value in headers.items() if key != "X-Northstar-Operator"
        }
        assert http.post(path, json=body, headers=missing_operator).status_code == 400
        assert (
            http.post(
                path, json=body, headers=headers | {"X-Northstar-Operator": "arbitrary"}
            ).status_code
            == 409
        )
        for expiry, owner in (
            (datetime.now(UTC) - timedelta(seconds=1), status["runtime_id"]),
            (datetime.now(UTC) + timedelta(seconds=90), status["runtime_id"]),
            (datetime.now(UTC) + timedelta(seconds=30), str(uuid4())),
        ):
            headers["X-Live-Runtime-ID"] = owner
            headers["X-Live-Expires-At"] = expiry.isoformat()
            assert http.post(path, json=body, headers=headers).status_code == 409
        headers["X-Live-Runtime-ID"] = status["runtime_id"]
        headers["X-Northstar-Protocol"] = "another-release"
        assert http.post(path, json=body, headers=headers).status_code == 409
        headers["X-Northstar-Protocol"] = PROTOCOL_VERSION
        oversized = http.post(path, content=b" " * 16385, headers=headers)
        assert oversized.status_code == 413
        invalid = http.post(path, json={**body, "credentials": "never-echo-me"}, headers=headers)
        assert invalid.status_code == 422 and "never-echo-me" not in invalid.text
        with pytest.raises(LookupError):
            client.command(UUID(headers["X-Live-Command-ID"]))
        assert client.broker.baseline_context(source)["baseline"] is None


@pytest.mark.parametrize("offset", [-30, 30])
def test_stale_or_future_runtime_observation_cannot_authorize_commands(offset: int) -> None:
    posts = []

    def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
        return httpx.Response(
            200,
            json={"status": "AVAILABLE", "runtime_id": str(uuid4())},
            headers={
                "X-Northstar-Protocol": PROTOCOL_VERSION,
                "X-Northstar-Operator": "maintenance",
                "X-Live-Runtime-ID": str(uuid4()),
                "X-Live-Observed-At": (datetime.now(UTC) + timedelta(seconds=offset)).isoformat(),
            },
        )

    client = LiveClient(
        "http://localhost",
        LiveAuth("r" * 48, "c" * 48),
        transport=httpx.MockTransport(transport),
    )
    try:
        with pytest.raises(RuntimeUnavailable, match="invalid observation"):
            client.status()
        assert client.last_observation is None and posts == []
    finally:
        client.close()


def test_response_loss_is_queried_after_console_and_live_restart_without_repeating_effect(
    live_engine: Engine, tmp_path: Path
) -> None:
    source = saved_query(live_engine)
    auth = LiveAuth("r" * 48, "c" * 48)
    library = DataLibrary(live_engine, SourceFiles(tmp_path))
    app = create_app(live_engine, library, auth)
    identifier = uuid4()
    sends = 0
    with TestClient(app) as http:

        def lose_acknowledgement(request: httpx.Request) -> httpx.Response:
            nonlocal sends
            response = http.request(
                request.method, str(request.url), content=request.content, headers=request.headers
            )
            if request.method == "POST":
                sends += 1
                raise httpx.ReadError("synthetic response loss", request=request)
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content
            )

        caller = LiveClient(
            "http://localhost", auth, transport=httpx.MockTransport(lose_acknowledgement)
        )
        try:
            with pytest.raises(CommandUnknown) as lost:
                caller.broker.establish_baseline(source, request_id=identifier)
            assert lost.value.request_id == identifier
            assert str(lost.value.runtime_id) == caller.status()["runtime_id"]
        finally:
            caller.close()
        # Reopening a Console reconstructs the client, not the owner's state.
        reopened = LiveClient("http://localhost", auth, client=TestClient(app))
        try:
            receipt = reopened.command(identifier)
            assert receipt["status"] == "COMPLETED"
            baseline = reopened.broker.establish_baseline(source, request_id=identifier)
            assert baseline == receipt["result"] and sends == 1
            with pytest.raises(ValueError, match="different input"):
                reopened.broker.establish_baseline(uuid4(), request_id=identifier)
            owner_before = reopened.status()["runtime_id"]
        finally:
            reopened.close()
        assert (
            LiveClient("http://localhost", auth, client=http).status()["runtime_id"] == owner_before
        )
    # A new Live owns observations, but a past receipt remains an immutable acknowledgement.
    with TestClient(create_app(live_engine, library, auth)) as restarted_http:
        restarted = LiveClient("http://localhost", auth, client=restarted_http)
        assert restarted.status()["runtime_id"] != owner_before
        assert restarted.broker.establish_baseline(source, request_id=identifier) == baseline
        assert restarted.streams.list() == []


@pytest.mark.parametrize("failure", [RuntimeError, ValueError, LookupError])
def test_interrupted_effect_stays_unknown_and_old_page_never_retargets_new_live(
    live_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
) -> None:
    source = saved_query(live_engine)
    auth = LiveAuth("r" * 48, "c" * 48)
    library = DataLibrary(live_engine, SourceFiles(tmp_path))
    app = create_app(live_engine, library, auth)
    original = app.state.owner.baselines.establish
    attempts = 0

    def interrupt_after_commit(source_batch_id: UUID, *, request_id: UUID) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        original(source_batch_id, request_id=request_id)
        raise failure("synthetic-private-value")

    monkeypatch.setattr(app.state.owner.baselines, "establish", interrupt_after_commit)
    identifier = uuid4()
    with TestClient(app) as http:
        client = LiveClient("http://localhost", auth, client=http)
        owner_before = UUID(client.status()["runtime_id"])
        with pytest.raises(CommandUnknown):
            client.broker.establish_baseline(source, request_id=identifier)
        receipt = client.command(identifier)
        assert receipt["status"] == "UNKNOWN" and "synthetic-private-value" not in str(receipt)
        with pytest.raises(CommandUnknown):
            client.broker.establish_baseline(source, request_id=identifier)
        assert attempts == 1
    with TestClient(create_app(live_engine, library, auth)) as http:
        client = LiveClient("http://localhost", auth, client=http)
        old_page = client.for_runtime(owner_before)
        assert client.broker.baseline_context(source)["baseline"]["baseline_id"] == str(identifier)
        with pytest.raises(CommandUnknown):
            client.broker.establish_baseline(source, request_id=identifier)
        new_command = uuid4()
        with pytest.raises(RuntimeUnavailable, match="runtime changed"):
            old_page.streams.control(uuid4(), "STOP", request_id=new_command)
        with pytest.raises(LookupError):
            client.command(new_command)
        old_page.close()
        assert client.status()["status"] == "AVAILABLE"


def test_missing_runtime_keeps_last_observation_but_never_returns_it_as_live(
    live_engine: Engine, tmp_path: Path
) -> None:
    auth = LiveAuth("r" * 48, "c" * 48)
    app = create_app(live_engine, DataLibrary(live_engine, SourceFiles(tmp_path)), auth)
    available = True
    with TestClient(app) as http:

        def transport(request: httpx.Request) -> httpx.Response:
            if not available:
                raise httpx.ConnectError("synthetic runtime outage", request=request)
            response = http.request(request.method, str(request.url), headers=request.headers)
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content
            )

        client = LiveClient("http://localhost", auth, transport=httpx.MockTransport(transport))
        try:
            client.status()
            observed = client.last_observation
            available = False
            with pytest.raises(RuntimeUnavailable):
                client.streams.list()
            assert client.last_observation == observed
            with pytest.raises(RuntimeUnavailable):
                client.streams.control(uuid4(), "STOP", request_id=uuid4())
        finally:
            client.close()


def test_startup_verifies_retained_stream_without_reactivating_previous_code(
    live_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from northstar_quant.live.materials import StrategyMaterials
    from northstar_quant.live.streams import LiveStreams
    from tests.live.test_streams import prepare, start

    library, source, configuration, calls = prepare(live_engine, tmp_path, monkeypatch)
    streams = LiveStreams(live_engine, library)
    try:
        start(streams, source, configuration, uuid4())
        assert calls["ready"].wait(3)
    finally:
        streams.close()
    before = calls["count"]
    # A different installation may inspect fixed evidence, but cannot execute
    # the strategy artifact produced by the prior installation.
    monkeypatch.setattr("northstar_quant.strategies.artifacts.code_revision", lambda: "b" * 40)
    auth = LiveAuth("r" * 48, "c" * 48)
    app = create_app(live_engine, library, auth)
    with TestClient(app) as http:
        client = LiveClient("http://localhost", auth, client=http)
        assert client.status()["order_sending"] is False
    assert calls["count"] == before
    with pytest.raises(ValueError, match="matching its installed Git revision"):
        StrategyMaterials(live_engine).get_configuration(configuration)
    another = LiveStreams(live_engine, library)
    try:
        with pytest.raises(ValueError, match="matching its installed Git revision"):
            start(another, source, configuration, uuid4())
    finally:
        another.close()
    assert calls["count"] == before
