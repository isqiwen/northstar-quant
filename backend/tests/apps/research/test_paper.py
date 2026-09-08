"""Research Paper commands preserve authorization and durable session state."""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine

from northstar_quant.apps.data_hub import create_app as data_app
from northstar_quant.apps.research import create_app as research_app
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from tests.apps.browser import ProtocolClient as TestClient
from tests.apps.browser import _browser_session, _upload_request


def test_paper_commands_require_browser_session_and_preserve_fixed_state(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    del clean_database
    specification = {
        "exchange": "SHFE",
        "symbol": "RB2605",
        "product": "RB",
        "timezone": "Asia/Shanghai",
        "currency": "CNY",
        "quantity_unit": "TON",
        "price_tick": "1",
        "multiplier": "10",
        "trading_day": "2026-01-07",
        "session_open": "2026-01-07T01:00:00Z",
        "session_close": "2026-01-07T01:04:00Z",
        "source_name": "paper-http-check",
        "source_reference": "generated observations for the persistent Paper HTTP path",
        "availability_basis": "SYNTHETIC",
        "availability_note": "Generated bars; availability is a simulation assumption.",
    }
    lines = ["event_time,available_at,source_record_id,open,high,low,close,volume"]
    for index, price in enumerate([100, 101, 103, 102]):
        lines.append(
            f"2026-01-07T01:0{index}:00Z,2026-01-07T01:0{index + 1}:00Z,"
            f"paper-{index},{price},{price},{price},{price},100"
        )
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    application = research_app(postgres_engine, library)
    with TestClient(application, base_url="http://127.0.0.1") as client:
        _browser_session(client)
        with TestClient(data_app(postgres_engine, library), base_url="http://127.0.0.1") as data:
            _browser_session(data)
            imported = data.post(
                "/api/import",
                json=_upload_request(("\n".join(lines) + "\n").encode(), specification),
            )
        assert imported.status_code == 200, imported.text
        assert imported.json()["status"] == "PUBLISHED"
        del client.headers["X-Northstar-CSRF"]
        configuration_request = {"name": "fixed momentum", "config": {}}
        # Loopback and even an explicit same-origin header do not authorize a command.
        assert (
            client.post(
                "/api/configurations",
                json=configuration_request,
                headers={"Origin": "http://127.0.0.1"},
            ).status_code
            == 403
        )
        page = client.get("/api/browser-session")
        assert page.status_code == 200
        assert "HttpOnly" in page.headers["set-cookie"]
        assert "SameSite=strict" in page.headers["set-cookie"]
        csrf = page.json()["csrf"]
        assert client.post("/api/configurations", json=configuration_request).status_code == 403
        client.headers["X-Northstar-CSRF"] = csrf
        saved_configuration = client.post("/api/configurations", json=configuration_request)
        assert saved_configuration.status_code == 201, saved_configuration.text
        configuration = saved_configuration.json()
        assert client.get("/api/configurations").json() == [configuration]
        # A second browser shares the running server, not a second application lifespan.
        with closing(TestClient(application, base_url="http://127.0.0.1")) as stranger:
            # A token copied from another browser without its session cookie is insufficient.
            stranger.get("/api/browser-session")
            assert (
                stranger.post(
                    "/api/configurations",
                    json=configuration_request,
                    headers={"X-Northstar-CSRF": csrf},
                ).status_code
                == 403
            )

        create_request = {
            "snapshot_id": imported.json()["snapshot_id"],
            "configuration_id": configuration["configuration_id"],
            "request_id": str(uuid4()),
        }
        created = client.post("/api/paper", json=create_request)
        assert created.status_code == 201, created.text
        session = created.json()
        session_id = session["session_id"]
        assert session["mode"] == "paper"
        assert session["input_type"] == "FILE_REPLAY"
        assert session["status"] == "PAUSED"
        assert session["cursor"] == 0
        assert session["pending_order"] is None
        assert session["equity_curve"] == []
        assert client.get(f"/paper/{session_id}").status_code == 404
        assert client.post("/api/paper", json=create_request).json()["session_id"] == session_id
        assert len(client.get("/api/paper").json()) == 1
        assert (
            client.post(
                "/api/paper", json={**create_request, "account": {"equity": "999999"}}
            ).status_code
            == 422
        )

        command = {"request_id": str(uuid4())}
        endpoint = f"/api/paper/{session_id}/advance"
        assert client.post(endpoint, json={**command, "cursor": 3}).status_code == 422
        assert (
            client.post(
                endpoint, json=command, headers={"Origin": "https://another-origin.example"}
            ).status_code
            == 403
        )
        advanced = client.post(endpoint, json=command)
        assert advanced.status_code == 200, advanced.text
        assert client.post(endpoint, json=command).json() == advanced.json()
        persisted = client.get(f"/api/paper/{session_id}").json()
        assert persisted["cursor"] == 1
        assert len(persisted["equity_curve"]) == 1
        assert persisted["configuration"] == configuration
        changed = client.post(
            "/api/configurations",
            json={"name": "fixed momentum", "config": {"simulation": {"fee_per_lot": "3"}}},
        )
        assert changed.status_code == 201, changed.text
        assert changed.json()["configuration_id"] != configuration["configuration_id"]
        assert client.get(f"/api/paper/{session_id}").json() == persisted
        cookie = client.cookies.get("northstar_research_session")
        assert cookie is not None

    # Recreating the application preserves DB progress but not browser command authority.
    with TestClient(research_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        client.cookies.set("northstar_workspace_session", cookie)
        client.headers["X-Northstar-CSRF"] = csrf
        assert client.get(f"/api/paper/{session_id}").json() == persisted
        assert client.post(endpoint, json={"request_id": str(uuid4())}).status_code == 403
        client.cookies.clear()
        page = client.get("/api/browser-session")
        assert page.status_code == 200
        client.headers["X-Northstar-CSRF"] = page.json()["csrf"]
        for _ in range(3):
            advanced = client.post(endpoint, json={"request_id": str(uuid4())})
            assert advanced.status_code == 200, advanced.text
        completed = client.get(f"/api/paper/{session_id}").json()
        assert completed["cursor"] == completed["total_inputs"] == 4
        assert completed["status"] == "COMPLETED"
        assert not completed["can_advance"]
        assert completed["configuration"] == configuration
        assert len(completed["equity_curve"]) == 4
        assert Decimal(completed["summary"]["total_fees"]) > 0
        assert client.get(f"/paper/{session_id}").status_code == 404
        assert client.get("/paper").status_code == 404
