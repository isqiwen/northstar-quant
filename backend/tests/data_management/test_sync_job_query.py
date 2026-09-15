"""All matching problems remain reachable beyond the overview's recent fifty."""

import pytest
from sqlalchemy import text

from northstar_quant.data_management.tushare import job_query, planning
from tests.data_management.test_tushare import automatic as automatic


def test_filters_and_pages_reach_every_problem_without_duplicates(automatic):
    with automatic._engine.begin() as c:
        for i in range(73):
            planning.enqueue(
                c, "daily", f"CONTRACT{i}", {"ts_code": str(i)}, "2026-09-01", "2026-09-01"
            )
        c.execute(text("UPDATE data_sync_jobs SET status='BLOCKED',updated_at='2026-09-01'"))
        planning.enqueue(c, "1min", "OTHER", {}, "2026-09-01", "2026-09-01")
    ids = []
    for offset in range(0, 80, 10):
        result = job_query.search(
            automatic._engine, dataset="daily", status="BLOCKED", offset=offset, limit=10
        )
        assert result["total"] == 73
        ids.extend(r["request_id"] for r in result["items"])
        assert all(r["dataset"] == "daily" and r["status"] == "BLOCKED" for r in result["items"])
    assert len(ids) == len(set(ids)) == 73
    assert (
        job_query.search(
            automatic._engine, dataset="daily", status="VALIDATED", offset=0, limit=10
        )["items"]
        == []
    )
    with pytest.raises(ValueError):
        job_query.search(automatic._engine, dataset="daily' OR true", status="", offset=0, limit=10)


def test_job_query_uses_authenticated_protobuf_route(automatic):
    from northstar_quant.apps.data_hub import create_app
    from tests.apps.browser import ProtocolClient, login_response

    app = create_app(automatic._engine, automatic)
    request = dict(dataset="daily", status="BLOCKED", offset=0, limit=10)
    with ProtocolClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/sync/jobs/query", json=request).status_code == 401
        csrf = login_response(client).json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        result = client.post("/api/sync/jobs/query", json=request)
        assert result.status_code == 200, result.text
        assert result.json() == {"total": 0, "offset": 0, "limit": 10, "items": []}
        assert (
            client.post("/api/sync/jobs/query", json={**request, "limit": 101}).status_code == 422
        )
