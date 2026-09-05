"""Protect private credentials and explicit, fixed broker-query commands."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_broker_baselines import saved_query

from northstar_quant.broker import ctp
from northstar_quant.broker.records import QueryCapture
from northstar_quant.broker.settings import credential_status, load_credentials
from northstar_quant.broker.workspace import BrokerWorkspace
from northstar_quant.cli import main
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


def test_browser_baseline_commands_are_private_local_and_preserve_original_queries(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    monkeypatch.delenv("NORTHSTAR_SIMNOW_CONFIG", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("local baseline commands must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.workspace.load_credentials", forbidden)
    source = saved_query(postgres_engine)
    original = BrokerWorkspace(postgres_engine).get(source)
    baseline_id, check_id = uuid4(), uuid4()
    baseline_payload = {"source_batch_id": str(source), "request_id": str(baseline_id)}
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    context_url = f"/api/broker/queries/{source}/baseline-context"
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        assert client.get(context_url).status_code == 403
        assert client.get(f"/api/broker/baseline-checks/{check_id}").status_code == 403
        assert client.post("/api/broker/baselines", json=baseline_payload).status_code == 403
        assert client.post("/api/broker/baseline-checks", json={}).status_code == 403
        page = client.get(f"/broker/{source}")
        assert page.status_code == 200
        context = client.get(context_url).json()
        assert context == {
            "eligibility": {"allowed": True, "reasons": []},
            "baseline": None,
            "checks": [],
        }
        assert client.post("/api/broker/baselines", json=baseline_payload).status_code == 403
        token = re.search(r'<meta name="northstar-csrf" content="([^"]+)">', page.text)
        assert token is not None
        client.headers["X-Northstar-CSRF"] = token.group(1)
        assert (
            client.post(
                "/api/broker/baselines",
                json=baseline_payload,
                headers={"Origin": "https://elsewhere.test"},
            ).status_code
            == 403
        )
        for extra in ({"funds": {"Balance": "1"}}, {"positions": []}, {"source_batch_id": False}):
            assert (
                client.post("/api/broker/baselines", json=baseline_payload | extra).status_code
                == 422
            )
        response = client.post("/api/broker/baselines", json=baseline_payload)
        assert response.status_code == 200, response.text
        baseline = response.json()
        assert baseline["baseline_id"] == str(baseline_id)
        assert baseline["status"] == "BASELINE_RECORDED"
        assert client.post("/api/broker/baselines", json=baseline_payload).json() == baseline
        check_payload = {
            "baseline_id": str(baseline_id),
            "query_batch_id": str(source),
            "request_id": str(check_id),
        }
        assert client.post("/api/broker/baseline-checks", json=check_payload).status_code == 422
        later = saved_query(postgres_engine, money={"Balance": "99999.9"}, position=True)
        check_payload["query_batch_id"] = str(later)
        assert (
            client.post(
                "/api/broker/baseline-checks",
                json=check_payload | {"observed": {"Balance": "100000"}},
            ).status_code
            == 422
        )
        response = client.post("/api/broker/baseline-checks", json=check_payload)
        assert response.status_code == 200, response.text
        check = response.json()
        assert check["status"] == "DIFFERENCES"
        assert check["reconciliation"] == "UNRECONCILED"
        assert check["execution"] == {"order_sending": False, "cancel_sending": False}
        assert client.post("/api/broker/baseline-checks", json=check_payload).json() == check
        assert client.get(f"/api/broker/baseline-checks/{check_id}").json() == check
        page = client.get(f"/broker/{later}")
        assert page.status_code == 200
        assert "观察字段或账户活动发生变化" in page.text
        assert "cu2610" in page.text and "不是账本对账通过" in page.text
        assert 'data-broker-local="compare"' not in page.text
        assert client.get(f"/api/broker/queries/{source}").json() == original
        assert len(client.get("/api/broker/queries").json()) == 2
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as restarted:
        assert restarted.get(context_url).status_code == 403
        assert restarted.get(f"/broker/{source}").status_code == 200
        context = restarted.get(context_url).json()
        assert context["baseline"] == baseline and context["checks"] == [check]
        assert restarted.get(f"/api/broker/queries/{source}").json() == original


def test_cli_baseline_and_comparison_use_saved_evidence_without_credentials(
    postgres_engine: Engine,
    clean_database: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del clean_database
    monkeypatch.delenv("NORTHSTAR_SIMNOW_CONFIG", raising=False)
    monkeypatch.setenv(
        "NORTHSTAR_DATABASE_URL", postgres_engine.url.render_as_string(hide_password=False)
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("local baseline commands must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.workspace.load_credentials", forbidden)
    source = saved_query(postgres_engine)
    baseline_id = uuid4()
    command = ["broker-baseline", str(source), "--request-id", str(baseline_id)]
    assert main(command) == 0
    baseline = json.loads(capsys.readouterr().out)
    assert baseline["status"] == "BASELINE_RECORDED"
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out) == baseline
    later = saved_query(postgres_engine)
    assert main(["broker-compare", str(baseline_id), str(later), "--request-id", str(uuid4())]) == 0
    check = json.loads(capsys.readouterr().out)
    assert check["status"] == "MATCHED" and check["reconciliation"] == "UNRECONCILED"
    assert main(["broker-baseline-context", str(later)]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context["baseline"] == baseline and context["checks"] == [check]
    incomplete = saved_query(postgres_engine, money={"Available": None})
    assert (
        main(["broker-compare", str(baseline_id), str(incomplete), "--request-id", str(uuid4())])
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"


def test_browser_position_ledger_requires_local_commands_and_independent_evidence(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    monkeypatch.delenv("NORTHSTAR_SIMNOW_CONFIG", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("position ledger commands must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.workspace.load_credentials", forbidden)
    workspace = BrokerWorkspace(postgres_engine)
    origin = saved_query(postgres_engine)
    baseline_id, entry_id, check_id = uuid4(), uuid4(), uuid4()
    workspace.establish_baseline(origin, request_id=baseline_id)
    source = saved_query(postgres_engine)
    before_entry = saved_query(postgres_engine)
    original = workspace.get(source)
    entry_payload = {
        "baseline_id": str(baseline_id),
        "source_batch_id": str(source),
        "request_id": str(entry_id),
    }
    check_payload = {
        "entry_id": str(entry_id),
        "query_batch_id": str(source),
        "request_id": str(check_id),
    }
    context_url = f"/api/broker/queries/{source}/ledger-context"
    entry_url = f"/api/broker/position-entries/{entry_id}"
    check_url = f"/api/broker/position-checks/{check_id}"
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        for url in (context_url, entry_url, check_url):
            assert client.get(url).status_code == 403
        assert client.post("/api/broker/position-entries", json=entry_payload).status_code == 403
        assert client.post("/api/broker/position-checks", json=check_payload).status_code == 403
        page = client.get(f"/broker/{source}")
        assert page.status_code == 200
        baseline_page = client.get(f"/broker/{origin}")
        assert 'data-broker-ledger="ingest"' not in baseline_page.text
        assert "成交入账需要基准固定之后发起的查询" in baseline_page.text
        assert client.get(context_url).json()["current"] is None
        assert client.post("/api/broker/position-entries", json=entry_payload).status_code == 403
        assert client.post("/api/broker/position-checks", json=check_payload).status_code == 403
        token = re.search(r'<meta name="northstar-csrf" content="([^"]+)">', page.text)
        assert token is not None
        client.headers["X-Northstar-CSRF"] = token.group(1)
        assert (
            client.post(
                "/api/broker/position-entries",
                json=entry_payload,
                headers={"Origin": "https://elsewhere.test"},
            ).status_code
            == 403
        )
        for extra in ({"positions": []}, {"fills": []}, {"source_batch_id": False}):
            assert (
                client.post("/api/broker/position-entries", json=entry_payload | extra).status_code
                == 422
            )
        response = client.post("/api/broker/position-entries", json=entry_payload)
        assert response.status_code == 200, response.text
        entry = response.json()
        assert entry["status"] == "READY"
        assert entry["new_fill_count"] == 0
        assert client.post("/api/broker/position-entries", json=entry_payload).json() == entry
        assert client.get(entry_url).json() == entry
        page = client.get(f"/broker/{source}")
        assert 'data-broker-ledger="ingest"' not in page.text
        assert 'data-broker-ledger="compare"' not in page.text
        assert "不能自行证明一致" in page.text
        earlier_page = client.get(f"/broker/{before_entry}")
        assert 'data-broker-ledger="compare"' not in earlier_page.text
        assert "在目标记录固定前已开始" in earlier_page.text
        assert client.post("/api/broker/position-checks", json=check_payload).status_code == 422
        later = saved_query(postgres_engine)
        check_payload["query_batch_id"] = str(later)
        assert (
            client.post(
                "/api/broker/position-checks", json=check_payload | {"observed_positions": []}
            ).status_code
            == 422
        )
        response = client.post("/api/broker/position-checks", json=check_payload)
        assert response.status_code == 200, response.text
        check = response.json()
        assert check["status"] == "MATCHED"
        assert check["scope"] == "POSITION_QUANTITIES_ONLY"
        assert check["reconciliation"] == "UNRECONCILED"
        assert check["execution"] == {"order_sending": False, "cancel_sending": False}
        assert client.post("/api/broker/position-checks", json=check_payload).json() == check
        assert client.get(check_url).json() == check
        page = client.get(f"/broker/{later}")
        assert page.status_code == 200
        assert "数量相同（仅持仓数量范围）" in page.text
        assert "费用、资金流和结算账本尚未建立" in page.text
        assert 'data-broker-ledger="compare"' not in page.text
        context = client.get(f"/api/broker/queries/{later}/ledger-context").json()
        assert context["current_check"] == check
        assert context["source_entry"] is None
        assert client.get(f"/api/broker/queries/{source}").json() == original
        assert len(client.get("/api/broker/queries").json()) == 4
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as restarted:
        assert restarted.get(context_url).status_code == 403
        assert restarted.get(f"/broker/{source}").status_code == 200
        context = restarted.get(context_url).json()
        assert context["current"] == entry and context["source_entry"] == entry
        assert context["checks"] == [check]
        assert restarted.get(entry_url).json() == entry
        assert restarted.get(check_url).json() == check
        assert restarted.get(f"/api/broker/queries/{source}").json() == original


def test_cli_position_ledger_does_not_turn_unknown_observations_into_success(
    postgres_engine: Engine,
    clean_database: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del clean_database
    monkeypatch.delenv("NORTHSTAR_SIMNOW_CONFIG", raising=False)
    monkeypatch.setenv(
        "NORTHSTAR_DATABASE_URL", postgres_engine.url.render_as_string(hide_password=False)
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("position ledger commands must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.workspace.load_credentials", forbidden)
    baseline_id, entry_id = uuid4(), uuid4()
    source = saved_query(postgres_engine)
    BrokerWorkspace(postgres_engine).establish_baseline(source, request_id=baseline_id)
    source = saved_query(postgres_engine)
    command = ["broker-ingest", str(baseline_id), str(source), "--request-id", str(entry_id)]
    assert main(command) == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["status"] == "READY"
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out) == entry
    later = saved_query(postgres_engine)
    assert main(["broker-positions", str(entry_id), str(later), "--request-id", str(uuid4())]) == 0
    check = json.loads(capsys.readouterr().out)
    assert check["status"] == "MATCHED" and check["reconciliation"] == "UNRECONCILED"
    assert main(["broker-ledger", str(later)]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context["current"] == entry and context["current_check"] == check
    incomplete = saved_query(postgres_engine, failure="SDK_PROCESS_EXITED")
    assert (
        main(["broker-positions", str(entry_id), str(incomplete), "--request-id", str(uuid4())])
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"
    assert (
        main(["broker-ingest", str(baseline_id), str(incomplete), "--request-id", str(uuid4())])
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"
