"""Data Hub publication feeds reproducible Research results across app restarts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from northstar_quant.apps.data_hub import create_app as data_app
from northstar_quant.apps.research import create_app as research_app
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from tests.apps.browser import _browser_session, _upload_request


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
    with (
        TestClient(
            research_app(postgres_engine, DataLibrary(postgres_engine, archive)),
            base_url="http://127.0.0.1",
        ) as client,
        TestClient(
            data_app(postgres_engine, DataLibrary(postgres_engine, archive)),
            base_url="http://127.0.0.1",
        ) as data,
    ):
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/runs").json() == []
        assert client.get("/api/datasets").json() == []
        content = ("\n".join(lines) + "\n").encode("utf-8")
        upload_request = _upload_request(content, specification)
        assert data.post("/api/import", json=upload_request).status_code == 403
        _browser_session(client)
        _browser_session(data)
        assert client.post("/api/import", json=upload_request).status_code == 404
        assert data.post("/api/runs", json={}).status_code == 404
        imported = data.post("/api/import", json=upload_request)
        assert imported.status_code == 200, imported.text
        attempt = imported.json()
        assert attempt["status"] == "PUBLISHED"
        dataset = client.get(f"/api/datasets/{attempt['snapshot_id']}").json()
        assert dataset["bar_count"] == len(prices)
        source_id = attempt["source_id"]
        assert data.get(f"/api/sources/{source_id}/download").content == content
        assert data.get(f"/sources/{source_id}").status_code == 200
        assert data.get(f"/attempts/{attempt['attempt_id']}").status_code == 200
        assert (
            data.post("/api/import", json=upload_request).json()["attempt_id"]
            == attempt["attempt_id"]
        )
        # Acceptance itself persists a selectable dataset; no research run is required.
        assert client.get("/api/runs").json() == []
        assert client.get("/api/datasets").json()[0]["snapshot_id"] == dataset["snapshot_id"]
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
        with (
            TestClient(
                research_app(reopened, DataLibrary(reopened, SourceFiles(tmp_path / "archive"))),
                base_url="http://127.0.0.1",
            ) as client,
            TestClient(
                data_app(reopened, DataLibrary(reopened, SourceFiles(tmp_path / "archive"))),
                base_url="http://127.0.0.1",
            ) as data,
        ):
            assert client.get(f"/api/runs/{run_id}").json() == saved
            _browser_session(client)
            _browser_session(data)
            assert data.get(f"/api/sources/{source_id}/download").content == content
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
