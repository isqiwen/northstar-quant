"""Protect private credentials and explicit, fixed broker-query commands."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from northstar_quant.broker import ctp
from northstar_quant.broker.records import QueryCapture
from northstar_quant.broker.settings import credential_status, load_credentials
from northstar_quant.broker.workspace import BrokerWorkspace
from northstar_quant.data.files import SourceFiles
from northstar_quant.data.library import DataLibrary
from northstar_quant.web import create_app


def _credentials(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Generated test-only values, never the operator's private configuration.
    path.write_text(
        "NORTHSTAR_SIMNOW_USER_ID=123456\n"
        "NORTHSTAR_SIMNOW_PASSWORD=$(touch unwanted)#='literal'\n"
        "NORTHSTAR_SIMNOW_APP_ID=test_only\n"
        "NORTHSTAR_SIMNOW_AUTH_CODE=test_only\n"
    )
    path.chmod(0o600)
    monkeypatch.setenv("NORTHSTAR_SIMNOW_CONFIG", str(path))


def _failed_capture() -> QueryCapture:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return QueryCapture(
        started_at=now,
        finished_at=now,
        binding_name="ctpwrapper",
        binding_version="6.7.13",
        trader_api_version=None,
        market_api_version=None,
        events=(),
        failure_code="SDK_PROCESS_EXITED",
    )


def test_credentials_are_literal_private_and_absent_from_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials"
    _credentials(path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    credentials = load_credentials()
    assert credentials.password == "$(touch unwanted)#='literal'"
    assert not (tmp_path / "unwanted").exists()
    for value in (credentials.user_id, credentials.password, credentials.auth_code):
        assert value not in repr(credentials)
        assert value not in str(credential_status())
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        load_credentials()
    path.chmod(0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="unreadable"):
        load_credentials(alias)
    with path.open("a") as stream:
        stream.write("NORTHSTAR_SIMNOW_PASSWORD=another_secret\n")
    with pytest.raises(ValueError, match="duplicate") as rejected:
        load_credentials()
    assert "another_secret" not in str(rejected.value)


def test_query_failure_is_fixed_on_retry_and_blocks_concurrent_account_capture(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    _credentials(tmp_path / "credentials", monkeypatch)
    entered, release = Event(), Event()
    calls = 0

    def capture(*args: object, **kwargs: object) -> QueryCapture:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5)
        return _failed_capture()

    monkeypatch.setattr(ctp, "query_account", capture)
    workspace = BrokerWorkspace(postgres_engine)
    request_id = uuid4()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(workspace.query, "simnow_dev", "rb2610", request_id=request_id)
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError, match="already running"):
                workspace.query("simnow_dev", "rb2610", request_id=uuid4())
        finally:
            release.set()
        saved = future.result(timeout=5)
    assert saved["status"] == "FAILED"
    assert workspace.query("simnow_dev", "rb2610", request_id=request_id) == saved
    with pytest.raises(ValueError, match="different input"):
        workspace.query("simnow_trading", "rb2610", request_id=request_id)
    assert calls == 1
    monkeypatch.delenv("NORTHSTAR_SIMNOW_CONFIG")
    assert BrokerWorkspace(postgres_engine).get(request_id) == saved
    assert workspace.query("simnow_dev", "rb2610", request_id=request_id) == saved
    assert calls == 1


def test_broker_browser_requires_explicit_command_and_keeps_failure_evidence(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    monkeypatch.delenv("NORTHSTAR_SIMNOW_CONFIG", raising=False)
    calls = 0

    def capture(*args: object, **kwargs: object) -> QueryCapture:
        nonlocal calls
        calls += 1
        # No exception text containing third-party or credential bytes may escape.
        raise RuntimeError("native_secret_must_not_escape")

    monkeypatch.setattr(ctp, "query_account", capture)
    application = create_app(
        postgres_engine, DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    )
    payload = {"profile": "simnow_dev", "instrument": "rb2610", "request_id": str(uuid4())}
    with TestClient(application, base_url="http://127.0.0.1") as client:
        assert client.get("/api/broker/status").status_code == 403
        assert client.post("/api/broker/queries", json=payload).status_code == 403
        page = client.get("/broker")
        assert page.status_code == 200
        assert not client.get("/api/broker/status").json()["credentials"]["configured"]
        assert client.post("/api/broker/queries", json=payload).status_code == 403
        token = re.search(r'<meta name="northstar-csrf" content="([^"]+)">', page.text)
        assert token is not None
        client.headers["X-Northstar-CSRF"] = token.group(1)
        assert client.post("/api/broker/queries", json=payload).status_code == 422
        assert calls == 0
        _credentials(tmp_path / "credentials", monkeypatch)
        for change in (
            {"profile": "production"},
            {"td_front": "tcp://localhost:1234"},
            {"password": "never_accept_over_http"},
            {"instrument": "rb2610;injected"},
        ):
            assert client.post("/api/broker/queries", json=payload | change).status_code == 422
        assert calls == 0
        response = client.post("/api/broker/queries", json=payload)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["status"] == "FAILED"
        assert saved["capture"]["failure_code"] == "ADAPTER_FAILURE"
        assert "native_secret_must_not_escape" not in response.text
        assert saved["reconciliation"]["status"] == "UNRECONCILED"
        assert saved["execution"] == {"order_sending": False, "cancel_sending": False}
        assert client.post("/api/broker/queries", json=payload).json() == saved
        assert calls == 1
        assert client.get(f"/api/broker/queries/{payload['request_id']}").json() == saved
        page = client.get(f"/broker/{payload['request_id']}")
        assert page.status_code == 200
        assert "尚无完整响应，不能解释为空" in page.text
        assert client.get("/broker").status_code == 200
        assert len(client.get("/api/broker/queries").json()) == 1
