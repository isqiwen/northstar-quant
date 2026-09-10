"""Protect private credentials and explicit, fixed broker-query commands."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine

from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker import ctp
from northstar_quant.broker.events import QueryCapture
from northstar_quant.broker.queries import BrokerQueries
from northstar_quant.broker.settings import credential_status, load_credentials
from northstar_quant.cli import main
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from tests.accounting.test_baselines import saved_query
from tests.accounting.test_ledger import ledger_query, position, position_baseline, trade
from tests.apps.browser import ProtocolClient as TestClient
from tests.apps.browser import login_response
from tests.execution.test_orders import order


def test_saved_stream_catchup_rejects_missing_session_before_database_or_broker_access(
    live_web_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_access(*args: object, **kwargs: object) -> None:
        pytest.fail("unauthenticated catchup must not access a database or broker")

    monkeypatch.setattr(ctp, "query_account", forbidden_access)
    monkeypatch.setattr(ctp, "stream_account", forbidden_access)
    engine = create_engine("postgresql+psycopg://", creator=forbidden_access)
    try:
        library = DataLibrary(engine, SourceFiles(tmp_path / "archive"))
        with TestClient(live_web_app(engine, library), base_url="http://127.0.0.1") as client:
            path = f"/api/streams/{uuid4()}/account-catchup"
            payload = {"baseline_id": str(uuid4()), "through_sequence": 3}
            assert client.post(path, json=payload).status_code == 401
            assert (
                client.post(
                    path, json=payload, headers={"X-Northstar-CSRF": "unbound-token"}
                ).status_code
                == 401
            )
    finally:
        engine.dispose()


def _credentials(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Generated test-only values, never the operator's private configuration.
    monkeypatch.setenv("NORTHSTAR_SIMNOW_USER_ID", "123456")
    monkeypatch.setenv("NORTHSTAR_SIMNOW_PASSWORD", "$(touch unwanted)#='literal'")
    monkeypatch.setenv("NORTHSTAR_SIMNOW_APP_ID", "test_only")
    monkeypatch.setenv("NORTHSTAR_SIMNOW_AUTH_CODE", "test_only")


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
    monkeypatch.setenv("NORTHSTAR_SIMNOW_PASSWORD", "invalid\nsecret")
    with pytest.raises(ValueError) as rejected:
        load_credentials()
    assert "invalid" not in str(rejected.value)
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD")
    assert credential_status()["configured"] is False


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
    workspace = BrokerQueries(postgres_engine)
    request_id = uuid4()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(workspace.query, "rb2610", request_id=request_id)
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError, match="already running"):
                workspace.query("rb2610", request_id=uuid4())
        finally:
            release.set()
        saved = future.result(timeout=5)
    assert saved["status"] == "FAILED"
    assert workspace.query("rb2610", request_id=request_id) == saved
    with pytest.raises(ValueError, match="different input"):
        workspace.query("rb2611", request_id=request_id)
    assert calls == 1
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD")
    assert BrokerQueries(postgres_engine).get(request_id) == saved
    assert workspace.query("rb2610", request_id=request_id) == saved
    assert calls == 1


def test_broker_browser_requires_explicit_command_and_keeps_failure_evidence(
    live_web_app,
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    monkeypatch.setenv("NORTHSTAR_SIMNOW_USER_ID", "123456")
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD", raising=False)
    calls = 0

    def capture(*args: object, **kwargs: object) -> QueryCapture:
        nonlocal calls
        calls += 1
        # No exception text containing third-party or credential bytes may escape.
        raise RuntimeError("native_secret_must_not_escape")

    monkeypatch.setattr(ctp, "query_account", capture)
    application = live_web_app(
        postgres_engine, DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    )
    payload = {"instrument": "rb2610", "request_id": str(uuid4())}
    with TestClient(application, base_url="http://127.0.0.1") as client:
        assert client.get("/api/broker/status").status_code == 401
        assert client.post("/api/broker/queries", json=payload).status_code == 401
        page = login_response(client)
        assert page.status_code == 200
        assert not client.get("/api/broker/status").json()["credentials"]["configured"]
        assert client.post("/api/broker/queries", json=payload).status_code == 403
        token = page.json()["csrf"]
        client.headers["X-Northstar-CSRF"] = token
        client.headers["X-Live-Runtime-ID"] = client.get("/api/live/status").json()["runtime_id"]
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
        page = client.get("/api/browser-session")
        assert page.status_code == 200
        assert client.get("/broker").status_code == 404
        assert len(client.get("/api/broker/queries").json()) == 1


def test_browser_baseline_commands_are_private_local_and_preserve_original_queries(
    live_web_app,
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("local baseline commands must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.queries.load_credentials", forbidden)
    source = saved_query(postgres_engine)
    original = BrokerQueries(postgres_engine).get(source)
    baseline_id, check_id = uuid4(), uuid4()
    baseline_payload = {"source_batch_id": str(source), "request_id": str(baseline_id)}
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    context_url = f"/api/broker/queries/{source}/baseline-context"
    with TestClient(live_web_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        assert client.get(context_url).status_code == 401
        assert client.get(f"/api/broker/baseline-checks/{check_id}").status_code == 401
        assert client.post("/api/broker/baselines", json=baseline_payload).status_code == 401
        assert client.post("/api/broker/baseline-checks", json={}).status_code == 401
        page = login_response(client)
        assert page.status_code == 200
        context = client.get(context_url).json()
        assert context == {
            "eligibility": {"allowed": True, "reasons": []},
            "baseline": None,
            "checks": [],
        }
        assert client.post("/api/broker/baselines", json=baseline_payload).status_code == 403
        token = page.json()["csrf"]
        client.headers["X-Northstar-CSRF"] = token
        client.headers["X-Live-Runtime-ID"] = client.get("/api/live/status").json()["runtime_id"]
        assert (
            client.post(
                "/api/broker/baselines",
                json=baseline_payload,
                headers={"Origin": "https://elsewhere.test"},
            ).status_code
            == 403
        )
        for extra in (
            {"funds": {"Balance": "1"}},
            {"positions": []},
            {"source_batch_id": "not-a-uuid"},
        ):
            assert (
                client.post("/api/broker/baselines", json=baseline_payload | extra).status_code
                == 422
            )
        response = client.post(
            "/api/broker/baselines",
            json=baseline_payload,
            headers={"X-Northstar-Operator": "forged-browser-identity"},
        )
        assert response.status_code == 200, response.text
        baseline = response.json()
        assert baseline["baseline_id"] == str(baseline_id)
        assert baseline["status"] == "BASELINE_RECORDED"
        receipt = client.get(f"/api/live/commands/{baseline_id}")
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["request_id"] == str(baseline_id)
        assert receipt.json()["operator"] == "owner"
        assert receipt.json()["status"] == "COMPLETED"
        assert client.post("/api/broker/baselines", json=baseline_payload).json() == baseline
        check_payload = {
            "baseline_id": str(baseline_id),
            "query_batch_id": str(source),
            "request_id": str(check_id),
        }
        rejected = client.post(
            "/api/broker/baseline-checks", json=check_payload | {"request_id": str(uuid4())}
        )
        assert rejected.status_code == 503 and rejected.json()["status"] == "UNKNOWN"
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
        page = client.get("/api/browser-session")
        assert page.status_code == 200
        assert client.get(f"/api/broker/queries/{source}").json() == original
        assert len(client.get("/api/broker/queries").json()) == 2
    with TestClient(
        live_web_app(postgres_engine, library), base_url="http://127.0.0.1"
    ) as restarted:
        assert restarted.get(context_url).status_code == 401
        assert login_response(restarted).status_code == 200
        context = restarted.get(context_url).json()
        assert context["baseline"] == baseline and context["checks"] == [check]
        assert restarted.get(f"/api/broker/queries/{source}").json() == original


def test_cli_baseline_and_comparison_use_saved_evidence_without_credentials(
    live_client,
    tmp_path: Path,
    postgres_engine: Engine,
    clean_database: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del clean_database
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    monkeypatch.setattr(
        "northstar_quant.live.LiveClient.from_environment",
        lambda: live_client(postgres_engine, library),
    )
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD", raising=False)
    monkeypatch.setenv(
        "NORTHSTAR_DATABASE_URL", postgres_engine.url.render_as_string(hide_password=False)
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("local baseline commands must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.queries.load_credentials", forbidden)
    source = saved_query(postgres_engine)
    baseline_id = uuid4()
    command = ["advanced", "broker", "baseline", str(source), "--request-id", str(baseline_id)]
    assert main(command) == 0
    baseline = json.loads(capsys.readouterr().out)
    assert baseline["status"] == "BASELINE_RECORDED"
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out) == baseline
    later = saved_query(postgres_engine)
    assert (
        main(
            [
                "advanced",
                "broker",
                "compare",
                str(baseline_id),
                str(later),
                "--request-id",
                str(uuid4()),
            ]
        )
        == 0
    )
    check = json.loads(capsys.readouterr().out)
    assert check["status"] == "MATCHED" and check["reconciliation"] == "UNRECONCILED"
    assert main(["advanced", "broker", "baseline-context", str(later)]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context["baseline"] == baseline and context["checks"] == [check]
    incomplete = saved_query(postgres_engine, money={"Available": None})
    assert (
        main(
            [
                "advanced",
                "broker",
                "compare",
                str(baseline_id),
                str(incomplete),
                "--request-id",
                str(uuid4()),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"


def test_browser_position_ledger_requires_local_commands_and_independent_evidence(
    live_web_app,
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("position ledger commands must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.queries.load_credentials", forbidden)
    workspace = BrokerQueries(postgres_engine)
    origin = saved_query(postgres_engine)
    baseline_id, entry_id, check_id = uuid4(), uuid4(), uuid4()
    BrokerBaselines(postgres_engine).establish(origin, request_id=baseline_id)
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
    with TestClient(live_web_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        for url in (context_url, entry_url, check_url):
            assert client.get(url).status_code == 401
        assert client.post("/api/broker/position-entries", json=entry_payload).status_code == 401
        assert client.post("/api/broker/position-checks", json=check_payload).status_code == 401
        page = login_response(client)
        assert page.status_code == 200
        client.get(f"/broker/{origin}")
        assert client.get(context_url).json()["current"] is None
        assert client.post("/api/broker/position-entries", json=entry_payload).status_code == 403
        assert client.post("/api/broker/position-checks", json=check_payload).status_code == 403
        token = page.json()["csrf"]
        client.headers["X-Northstar-CSRF"] = token
        client.headers["X-Live-Runtime-ID"] = client.get("/api/live/status").json()["runtime_id"]
        assert (
            client.post(
                "/api/broker/position-entries",
                json=entry_payload,
                headers={"Origin": "https://elsewhere.test"},
            ).status_code
            == 403
        )
        for extra in ({"positions": []}, {"fills": []}, {"source_batch_id": "not-a-uuid"}):
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
        page = client.get("/api/browser-session")
        client.get(f"/broker/{before_entry}")
        rejected = client.post(
            "/api/broker/position-checks", json=check_payload | {"request_id": str(uuid4())}
        )
        assert rejected.status_code == 503 and rejected.json()["status"] == "UNKNOWN"
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
        page = client.get("/api/browser-session")
        assert page.status_code == 200
        context = client.get(f"/api/broker/queries/{later}/ledger-context").json()
        assert context["current_check"] == check
        assert context["source_entry"] is None
        assert client.get(f"/api/broker/queries/{source}").json() == original
        assert len(client.get("/api/broker/queries").json()) == 4
    with TestClient(
        live_web_app(postgres_engine, library), base_url="http://127.0.0.1"
    ) as restarted:
        assert restarted.get(context_url).status_code == 401
        assert login_response(restarted).status_code == 200
        context = restarted.get(context_url).json()
        assert context["current"] == entry and context["source_entry"] == entry
        assert context["checks"] == [check]
        assert restarted.get(entry_url).json() == entry
        assert restarted.get(check_url).json() == check
        assert restarted.get(f"/api/broker/queries/{source}").json() == original


def test_cli_position_ledger_does_not_turn_unknown_observations_into_success(
    live_client,
    tmp_path: Path,
    postgres_engine: Engine,
    clean_database: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del clean_database
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    monkeypatch.setattr(
        "northstar_quant.live.LiveClient.from_environment",
        lambda: live_client(postgres_engine, library),
    )
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD", raising=False)
    monkeypatch.setenv(
        "NORTHSTAR_DATABASE_URL", postgres_engine.url.render_as_string(hide_password=False)
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("position ledger commands must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.queries.load_credentials", forbidden)
    baseline_id, entry_id = uuid4(), uuid4()
    source = saved_query(postgres_engine)
    BrokerBaselines(postgres_engine).establish(source, request_id=baseline_id)
    source = saved_query(postgres_engine)
    command = [
        "advanced",
        "broker",
        "ingest",
        str(baseline_id),
        str(source),
        "--request-id",
        str(entry_id),
    ]
    assert main(command) == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["status"] == "READY"
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out) == entry
    later = saved_query(postgres_engine)
    assert (
        main(
            [
                "advanced",
                "broker",
                "positions",
                str(entry_id),
                str(later),
                "--request-id",
                str(uuid4()),
            ]
        )
        == 0
    )
    check = json.loads(capsys.readouterr().out)
    assert check["status"] == "MATCHED" and check["reconciliation"] == "UNRECONCILED"
    assert main(["advanced", "broker", "ledger", str(later)]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context["current"] == entry and context["current_check"] == check
    order_command = [
        "advanced",
        "broker",
        "orders",
        check["check_id"],
        "--request-id",
        str(uuid4()),
    ]
    assert main(order_command) == 0
    order_check = json.loads(capsys.readouterr().out)
    assert order_check["status"] == "MATCHED"
    assert order_check["reconciliation"] == "UNRECONCILED"
    assert main(order_command) == 0
    assert json.loads(capsys.readouterr().out) == order_check
    incomplete = saved_query(postgres_engine, failure="SDK_PROCESS_EXITED")
    assert (
        main(
            [
                "advanced",
                "broker",
                "positions",
                str(entry_id),
                str(incomplete),
                "--request-id",
                str(uuid4()),
            ]
        )
        == 2
    )
    incomplete_check = json.loads(capsys.readouterr().out)
    assert incomplete_check["status"] == "UNKNOWN"
    assert (
        main(
            [
                "advanced",
                "broker",
                "orders",
                incomplete_check["check_id"],
                "--request-id",
                str(uuid4()),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"
    assert (
        main(
            [
                "advanced",
                "broker",
                "ingest",
                str(baseline_id),
                str(incomplete),
                "--request-id",
                str(uuid4()),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"


def test_browser_order_check_uses_fixed_inputs_without_credentials_or_manual_facts(
    live_web_app,
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("saved order checks must not load credentials or connect")

    monkeypatch.setattr(ctp, "query_account", forbidden)
    monkeypatch.setattr("northstar_quant.broker.queries.load_credentials", forbidden)
    workspace = BrokerQueries(postgres_engine)
    baseline_id = position_baseline(postgres_engine)
    source = ledger_query(
        postgres_engine, trades=(trade(),), positions=(position(),), orders=(order(),)
    )
    entry_id, position_check_id, order_check_id = uuid4(), uuid4(), uuid4()
    entry = BrokerLedger(postgres_engine).ingest(baseline_id, source, request_id=entry_id)
    later = ledger_query(
        postgres_engine, trades=(trade(),), positions=(position(),), orders=(order(),)
    )
    position_check = BrokerLedger(postgres_engine).compare(
        entry_id, later, request_id=position_check_id
    )
    original = workspace.get(later)
    payload = {"position_check_id": str(position_check_id), "request_id": str(order_check_id)}
    check_url = f"/api/broker/order-checks/{order_check_id}"
    context_url = f"/api/broker/queries/{later}/ledger-context"
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    with TestClient(live_web_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        assert client.get(check_url).status_code == 401
        assert client.post("/api/broker/order-checks", json=payload).status_code == 401
        page = login_response(client)
        assert page.status_code == 200
        assert client.get(context_url).json()["current_order_check"] is None
        assert client.post("/api/broker/order-checks", json=payload).status_code == 403
        token = page.json()["csrf"]
        client.headers["X-Northstar-CSRF"] = token
        client.headers["X-Live-Runtime-ID"] = client.get("/api/live/status").json()["runtime_id"]
        assert (
            client.post(
                "/api/broker/order-checks",
                json=payload,
                headers={"Origin": "https://elsewhere.test"},
            ).status_code
            == 403
        )
        for extra in (
            {"orders": []},
            {"fills": []},
            {"funds": {}},
            {"positions": []},
            {"position_check_id": "not-a-uuid"},
        ):
            assert client.post("/api/broker/order-checks", json=payload | extra).status_code == 422
        response = client.post("/api/broker/order-checks", json=payload)
        assert response.status_code == 200, response.text
        check = response.json()
        assert check["status"] == "MATCHED"
        assert check["scope"] == "ORDER_OBSERVATIONS_AND_RECORDED_FILLS"
        assert check["reconciliation"] == "UNRECONCILED"
        assert check["execution"] == {"order_sending": False, "cancel_sending": False}
        assert len(check["orders"]) == 1
        observed = check["orders"][0]
        assert observed["ledger_filled_lots"] == 2 and observed["fill_gap_lots"] == 0
        assert observed["ownership"] == "EXTERNAL_NOT_OWNED"
        assert observed["reservation_release"] == "NOT_AUTHORIZED"
        assert client.post("/api/broker/order-checks", json=payload).json() == check
        assert client.get(check_url).json() == check
        page = client.get("/api/browser-session")
        assert page.status_code == 200
        assert client.get(context_url).json()["current_order_check"] == check
        assert client.get(f"/api/broker/queries/{later}").json() == original
        assert client.get(f"/api/broker/position-entries/{entry_id}").json() == entry
        assert (
            client.get(f"/api/broker/position-checks/{position_check_id}").json() == position_check
        )
        assert len(client.get("/api/broker/queries").json()) == 3
    with TestClient(
        live_web_app(postgres_engine, library), base_url="http://127.0.0.1"
    ) as restarted:
        assert restarted.get(check_url).status_code == 401
        assert login_response(restarted).status_code == 200
        assert restarted.get(check_url).json() == check
        context = restarted.get(context_url).json()
        assert context["current_order_check"] == check and context["order_checks"] == [check]
