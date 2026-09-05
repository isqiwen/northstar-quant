"""Exercise the supported local workflow against durable PostgreSQL truth."""

from __future__ import annotations

import base64
import hashlib
import re
import tomllib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from northstar_quant.data.files import SourceFiles
from northstar_quant.data.library import DataLibrary
from northstar_quant.web import create_app


def _browser_session(client: TestClient, path: str = "/") -> None:
    page = client.get(path)
    assert page.status_code == 200, page.text
    token = re.search(r'<meta name="northstar-csrf" content="([^"]+)">', page.text)
    assert token is not None
    client.headers["X-Northstar-CSRF"] = token.group(1)


def _upload_request(content: bytes, specification: dict[str, object]) -> dict[str, object]:
    return {
        "content_base64": base64.b64encode(content).decode("ascii"),
        "filename": "minutes.csv",
        "source_name": specification["source_name"],
        "spec": specification,
        "request_id": str(uuid4()),
        "input_kind": "RECEIVED_CSV",
        "upstream_source_id": None,
        "transformation_note": None,
        "use_basis": "Generated HTTP workflow data, permitted for local research and retention.",
        "allow_retention": True,
        "allow_download": True,
    }


def test_import_research_and_reopen_preserve_complete_result(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    del clean_database
    start = datetime(2026, 1, 7, 1, 0, tzinfo=UTC)
    prices = [100, 101, 103, 102, 99, 98, 100, 101]
    lines = ["event_time,available_at,source_record_id,open,high,low,close,volume"]
    for index, price in enumerate(prices):
        at = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        available = (
            (start + timedelta(minutes=index + 1, seconds=2)).isoformat().replace("+00:00", "Z")
        )
        lines.append(f"{at},{available},bar-{index},{price},{price},{price},{price},100")
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
        "session_close": "2026-01-07T01:08:00Z",
        "source_name": "research-http-check",
        "source_reference": "generated in-memory HTTP workflow observations",
        "availability_basis": "SYNTHETIC",
        "availability_note": "Generated bars become available two seconds after completion.",
    }
    archive = SourceFiles(tmp_path / "archive")
    with TestClient(
        create_app(postgres_engine, DataLibrary(postgres_engine, archive)),
        base_url="http://127.0.0.1",
    ) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/runs").json() == []
        assert client.get("/api/datasets").json() == []
        content = ("\n".join(lines) + "\n").encode("utf-8")
        upload_request = _upload_request(content, specification)
        assert client.post("/api/import", json=upload_request).status_code == 403
        _browser_session(client)
        imported = client.post("/api/import", json=upload_request)
        assert imported.status_code == 200, imported.text
        attempt = imported.json()
        assert attempt["status"] == "PUBLISHED"
        dataset = client.get(f"/api/datasets/{attempt['snapshot_id']}").json()
        assert dataset["bar_count"] == len(prices)
        source_id = attempt["source_id"]
        assert client.get(f"/api/sources/{source_id}/download").content == content
        assert client.get(f"/sources/{source_id}").status_code == 200
        assert client.get(f"/attempts/{attempt['attempt_id']}").status_code == 200
        assert (
            client.post("/api/import", json=upload_request).json()["attempt_id"]
            == attempt["attempt_id"]
        )
        # Acceptance itself persists a selectable dataset; no research run is required.
        assert client.get("/api/runs").json() == []
        assert client.get("/api/datasets").json()[0]["snapshot_id"] == dataset["snapshot_id"]
        selected = client.get("/", params={"dataset": dataset["snapshot_id"]})
        assert selected.status_code == 200
        assert f'<option value="{dataset["snapshot_id"]}" selected>' in selected.text
        details = client.get(f"/api/datasets/{dataset['snapshot_id']}").json()
        assert details["import_spec"] == specification
        assert details["sources"][0]["source_name"] == specification["source_name"].upper()
        assert details["quality"]["imports"][0]["rows_accepted"] == len(prices)
        assert details["quality"]["minute"]["observed_count"] == len(prices)
        assert details["quality"]["minute"]["missing_observation_count"] == 0
        assert details["semantics"]["price_tick"] == specification["price_tick"]
        data_page = client.get(f"/datasets/{dataset['snapshot_id']}")
        assert data_page.status_code == 200
        assert specification["source_reference"] in data_page.text
        assert details["sources"][0]["content_hash"] in data_page.text
        assert "合成示例 · 非真实行情" in data_page.text
        request = {"snapshot_id": dataset["snapshot_id"], "config": {}}
        forbidden = client.post(
            "/api/runs", json=request, headers={"Origin": "https://another-origin.example"}
        )
        assert forbidden.status_code == 403
        submitted = client.post("/api/runs", json=request)
        assert submitted.status_code == 201, submitted.text
        run_id = submitted.json()["run_id"]
        saved = client.get(f"/api/runs/{run_id}").json()
        summary = saved["result"]["summary"]
        assert summary["fill_count"] >= 2
        assert Decimal(summary["total_fees"]) > 0
        assert Decimal(summary["ending_equity"]) == (
            Decimal(summary["initial_cash"])
            + Decimal(summary["realized_pnl"])
            + Decimal(summary["unrealized_pnl"])
            - Decimal(summary["total_fees"])
        )
        assert len(saved["result"]["equity_curve"]) == len(prices)
        assert saved["snapshot"]["content_hash"] == dataset["content_hash"]
        assert saved["config"] == saved["result"]["config"]
        assert saved["result"]["data"] == details
        assert len(saved["implementation_hash"]) == 64
        report = client.get(submitted.json()["url"])
        assert report.status_code == 200
        assert "RB2605" in report.text
        assert specification["source_reference"] in report.text
        assert details["quality"]["minute"]["evaluation_id"] in report.text
        assert f"/sources/{source_id}" in report.text
        assert "合成示例 · 非真实行情" in report.text
        assert client.get("/assets/app.js").status_code == 200
        repeated = client.post("/api/runs", json=request)
        assert repeated.json()["run_id"] == run_id
        assert len(client.get("/api/runs").json()) == 1

    reopened = create_engine(postgres_engine.url)
    try:
        with TestClient(
            create_app(reopened, DataLibrary(reopened, SourceFiles(tmp_path / "archive"))),
            base_url="http://127.0.0.1",
        ) as client:
            assert client.get(f"/api/runs/{run_id}").json() == saved
            _browser_session(client)
            assert client.get(f"/api/sources/{source_id}/download").content == content
            assert client.get("/api/datasets").json()[0]["snapshot_id"] == dataset["snapshot_id"]
            assert client.get(f"/api/datasets/{dataset['snapshot_id']}").json() == details
            changed = client.post(
                "/api/runs",
                json={"snapshot_id": dataset["snapshot_id"], "config": {"fee_per_lot": "3"}},
            )
            assert changed.status_code == 201, changed.text
            assert changed.json()["run_id"] != run_id
            changed_run = client.get(f"/api/runs/{changed.json()['run_id']}").json()
            assert changed_run["result"]["data"] == details
            assert len(client.get("/api/runs").json()) == 2
    finally:
        reopened.dispose()


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
    application = create_app(postgres_engine, library)
    with TestClient(application, base_url="http://127.0.0.1") as client:
        _browser_session(client)
        imported = client.post(
            "/api/import", json=_upload_request(("\n".join(lines) + "\n").encode(), specification)
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
        page = client.get("/paper")
        assert page.status_code == 200
        assert "HttpOnly" in page.headers["set-cookie"]
        assert "SameSite=strict" in page.headers["set-cookie"]
        token = re.search(r'<meta name="northstar-csrf" content="([^"]+)">', page.text)
        assert token is not None
        csrf = token.group(1)
        assert client.post("/api/configurations", json=configuration_request).status_code == 403
        client.headers["X-Northstar-CSRF"] = csrf
        saved_configuration = client.post("/api/configurations", json=configuration_request)
        assert saved_configuration.status_code == 201, saved_configuration.text
        configuration = saved_configuration.json()
        assert client.get("/api/configurations").json() == [configuration]
        with TestClient(application, base_url="http://127.0.0.1") as stranger:
            # A token copied from another browser without its session cookie is insufficient.
            stranger.get("/paper")
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
        assert client.get(f"/paper/{session_id}").status_code == 200
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
            "/api/configurations", json={"name": "fixed momentum", "config": {"fee_per_lot": "3"}}
        )
        assert changed.status_code == 201, changed.text
        assert changed.json()["configuration_id"] != configuration["configuration_id"]
        assert client.get(f"/api/paper/{session_id}").json() == persisted
        cookie = client.cookies.get("northstar_workspace_session")
        assert cookie is not None

    # Recreating the application preserves DB progress but not browser command authority.
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        client.cookies.set("northstar_workspace_session", cookie)
        client.headers["X-Northstar-CSRF"] = csrf
        assert client.get(f"/api/paper/{session_id}").json() == persisted
        assert client.post(endpoint, json={"request_id": str(uuid4())}).status_code == 403
        client.cookies.clear()
        page = client.get(f"/paper/{session_id}")
        assert page.status_code == 200
        token = re.search(r'<meta name="northstar-csrf" content="([^"]+)">', page.text)
        assert token is not None
        client.headers["X-Northstar-CSRF"] = token.group(1)
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
        assert client.get(f"/paper/{session_id}").status_code == 200
        assert client.get("/paper").status_code == 200


def test_archived_bytes_failures_reprocessing_and_download_permissions(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    del clean_database
    example = Path(__file__).resolve().parents[1] / "examples" / "intraday.toml"
    specification = dict(tomllib.loads(example.read_text())["source"])
    content = (example.parent / specification.pop("file")).read_bytes()
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    application = create_app(postgres_engine, library)
    with TestClient(application, base_url="http://127.0.0.1") as client:
        _browser_session(client)
        assert client.get("/api/sources").json() == []
        assert client.get("/api/attempts").json() == []

        # Even a decode failure retains exactly the received bytes, including BOM.
        broken_bytes = b"\xef\xbb\xbfevent_time,close\r\n\xff\x00\r\n"
        broken = client.post("/api/import", json=_upload_request(broken_bytes, specification))
        assert broken.status_code == 200, broken.text
        failed = broken.json()
        assert failed["status"] == "FAILED"
        assert failed["snapshot_id"] is None
        assert failed["error"]
        source_id = failed["source_id"]
        source = client.get(f"/api/sources/{source_id}").json()
        assert source["content_hash"] == hashlib.sha256(broken_bytes).hexdigest()
        assert source["byte_count"] == len(broken_bytes)
        assert client.get(f"/api/sources/{source_id}/download").content == broken_bytes
        assert client.get(f"/attempts/{failed['attempt_id']}").status_code == 200
        assert client.get("/api/datasets").json() == []
        with TestClient(application, base_url="http://127.0.0.1") as unauthenticated:
            assert unauthenticated.get(f"/api/sources/{source_id}/download").status_code == 403

        forbidden_payload = _upload_request(b"\xffnot-permitted-to-download", specification)
        forbidden_payload["allow_download"] = False
        private = client.post("/api/import", json=forbidden_payload)
        assert private.status_code == 200, private.text
        private_source = private.json()["source_id"]
        assert client.get(f"/api/sources/{private_source}/download").status_code == 403
        forbidden_payload = _upload_request(b"not-permitted-to-retain", specification)
        forbidden_payload["allow_retention"] = False
        source_count = len(client.get("/api/sources").json())
        refused = client.post("/api/import", json=forbidden_payload)
        assert refused.status_code == 422, refused.text
        assert refused.json()["rejection_id"]
        assert len(client.get("/api/sources").json()) == source_count
        assert refused.json()["rejection_id"] in client.get("/sources").text

        # Valid bytes with incorrect processing parameters are repairable without re-upload.
        invalid_spec = {**specification, "price_tick": "0"}
        received = client.post("/api/import", json=_upload_request(content, invalid_spec))
        assert received.status_code == 200, received.text
        rejected = received.json()
        assert rejected["status"] == "FAILED"
        assert rejected["snapshot_id"] is None
        source_id = rejected["source_id"]
        assert client.get("/api/datasets").json() == []
        corrected_request = {"spec": specification, "request_id": str(uuid4())}
        endpoint = f"/api/sources/{source_id}/reprocess"
        assert (
            client.post(endpoint, json={**corrected_request, "path": "/etc/passwd"}).status_code
            == 422
        )
        repaired = client.post(endpoint, json=corrected_request)
        assert repaired.status_code == 200, repaired.text
        published = repaired.json()
        assert published["status"] == "PUBLISHED"
        assert published["source_id"] == source_id
        assert published["attempt_id"] != rejected["attempt_id"]
        assert (
            client.post(endpoint, json=corrected_request).json()["attempt_id"]
            == published["attempt_id"]
        )
        assert client.get(f"/api/attempts/{rejected['attempt_id']}").json()["status"] == "FAILED"
        assert len(client.get("/api/datasets").json()) == 1
        assert client.get(f"/api/sources/{source_id}/download").content == content
        source = client.get(f"/api/sources/{source_id}").json()
        assert len(source["attempts"]) == 2
        assert source["content_hash"] == hashlib.sha256(content).hexdigest()
        lineage = client.get(f"/api/datasets/{published['snapshot_id']}/lineage").json()
        assert source_id in {item["source_id"] for item in lineage["sources"]}
        assert client.get(f"/attempts/{published['attempt_id']}").status_code == 200
        assert client.get(f"/datasets/{published['snapshot_id']}").status_code == 200
