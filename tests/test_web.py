"""Exercise the supported local workflow against durable PostgreSQL truth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from northstar_quant.web import create_app


def test_import_research_and_reopen_preserve_complete_result(
    postgres_engine: Engine, clean_database: None
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
    }
    with TestClient(create_app(postgres_engine), base_url="http://127.0.0.1") as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/runs").json() == []
        imported = client.post(
            "/api/import", json={"csv": "\n".join(lines) + "\n", "spec": specification}
        )
        assert imported.status_code == 200, imported.text
        dataset = imported.json()
        assert dataset["bar_count"] == len(prices)
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
        assert len(saved["implementation_hash"]) == 64
        report = client.get(submitted.json()["url"])
        assert report.status_code == 200
        assert "RB2605" in report.text
        assert 'class="equity-chart"' in report.text
        assert client.get("/assets/app.js").status_code == 200
        repeated = client.post("/api/runs", json=request)
        assert repeated.json()["run_id"] == run_id
        assert len(client.get("/api/runs").json()) == 1

    reopened = create_engine(postgres_engine.url)
    try:
        with TestClient(create_app(reopened), base_url="http://127.0.0.1") as client:
            assert client.get(f"/api/runs/{run_id}").json() == saved
            assert client.get("/").status_code == 200
            changed = client.post(
                "/api/runs",
                json={"snapshot_id": dataset["snapshot_id"], "config": {"fee_per_lot": "3"}},
            )
            assert changed.status_code == 201, changed.text
            assert changed.json()["run_id"] != run_id
            assert len(client.get("/api/runs").json()) == 2
    finally:
        reopened.dispose()
