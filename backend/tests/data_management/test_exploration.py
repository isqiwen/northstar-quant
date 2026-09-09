"""Pinned range reads, corruption refusal, coverage evidence and export authorization."""

import json
from uuid import UUID

import pytest
from sqlalchemy import text

from northstar_quant.apps.data_hub.application import create_app
from northstar_quant.data_management.exploration import catalog, quality, rows
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.storage_identity import initialize
from northstar_quant.data_management.tushare import acquisition, credentials, jobs, planning


@pytest.fixture
def published(postgres_engine, clean_database, tmp_path, monkeypatch):
    engine = postgres_engine
    market = tmp_path / "market"
    market.mkdir()
    storage_id = "8600b795-36d0-44b9-80e3-d3b22e805e92"
    initialize(market, storage_id)
    monkeypatch.setenv("NORTHSTAR_MARKET_DIR", str(market))
    monkeypatch.setenv("NORTHSTAR_MARKET_STORAGE_ID", storage_id)
    monkeypatch.setenv("NORTHSTAR_DATA_SECRET_DIR", str(tmp_path / "secret"))
    credentials.save("synthetic-explorer-token")
    library = DataLibrary(engine, SourceFiles(tmp_path / "source", min_free_bytes=0))
    with engine.begin() as c:
        c.execute(
            text("""UPDATE data_sync_settings SET enabled=true,revision=1,
            refresh_at=now()+interval '1 day',next_request_at=now()""")
        )
        c.execute(
            text("""INSERT INTO data_sync_contracts
            (ts_code,exchange,product,kind,details,planned_revision)
            VALUES ('RB2610.SHF','SHFE','RB','1',
            '{"list_date":"20200101","delist_date":"20300101"}',1)""")
        )
        c.execute(
            text("""INSERT INTO data_sync_calendar VALUES
            ('SHFE','2026-09-01',true),('SHFE','2026-09-02',false),('SHFE','2026-09-03',true)""")
        )
        planning.enqueue(
            c, "1min", "RB2610.SHF", {"ts_code": "RB2610.SHF"}, "2026-09-01", "2026-09-03"
        )
    response = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "trade_time", "open", "high", "low", "close", "vol", "oi"],
            "items": [
                [
                    "RB2610.SHF",
                    f"2026-09-0{d} 09:{m:02d}:00",
                    "3100.10",
                    "3101.20",
                    "3099",
                    "3101",
                    "2",
                    None,
                ]
                for d in (1, 3)
                for m in range(3)
            ],
        },
    }
    monkeypatch.setattr(acquisition, "fetch", lambda *args: json.dumps(response).encode())
    assert jobs.process_next(library)["status"] == "VALIDATED"
    return library, response


def test_fixed_pages_and_revision_do_not_mix(published):
    library, response = published
    engine = library._engine
    args = dict(dataset="1min", scope="RB2610.SHF", start="2026-09-01", end="2026-09-03")
    first = rows.read(engine, **args, receipt_ids=[], limit=2)
    ids = [UUID(v) for v in first["receipt_ids"]]
    assert first["total"] == 6
    assert first["rows"][0]["open"] == "3100.10"
    assert not first["export_allowed"]
    assert next(f for f in first["fields"] if f["key"] == "oi")["missing"] == 6
    response["data"]["items"][0][2] = "3100.20"
    with engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
        c.execute(text("UPDATE data_sync_settings SET next_request_at=now()"))
    assert jobs.process_next(library)["status"] == "VALIDATED"
    latest = rows.read(engine, **args, receipt_ids=[])
    assert latest["view_id"] != first["view_id"]
    pages = [rows.read(engine, **args, receipt_ids=ids, offset=n, limit=2) for n in (0, 2, 4)]
    assert {p["view_id"] for p in pages} == {first["view_id"]}
    exact = [r for p in pages for r in p["rows"]]
    assert len({r["_key"] for r in exact}) == 6
    assert exact[0]["open"] == "3100.10"
    assert len(catalog.versions(engine, **args)["rows"]) == 2
    with pytest.raises(ValueError, match="冲突"):
        rows.read(engine, **args, receipt_ids=ids + [UUID(latest["receipt_ids"][0])])
    with pytest.raises(ValueError, match="不属于"):
        rows.read(engine, **{**args, "scope": "FAKE.SHF"}, receipt_ids=ids)


def test_coverage_never_infers_complete_minutes(published):
    library, _ = published
    args = dict(dataset="1min", scope="RB2610.SHF", start="2026-09-01", end="2026-09-04")
    report = quality.coverage(library._engine, **args)
    assert [r["state"] for r in report["days"]] == ["RESPONSE_VALIDATED"] * 3 + ["NOT_DOWNLOADED"]
    assert report["days"][1]["calendar_open"] is False
    with library._engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='BLOCKED',error='synthetic missing'"))
    assert quality.coverage(library._engine, **args)["days"][0]["state"] == "BLOCKED"
    with library._engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET dataset='daily'"))
    assert (
        quality.coverage(library._engine, **{**args, "dataset": "daily"})["days"][1]["state"]
        == "CLOSED"
    )


def test_protocol_range_export_and_corruption_refusal(published):
    library, _ = published
    from tests.apps.browser import ProtocolClient

    with ProtocolClient(
        create_app(library._engine, library), base_url="http://127.0.0.1"
    ) as client:
        csrf = client.get("/api/browser-session").json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        selection = dict(
            dataset="1min",
            scope="RB2610.SHF",
            start="2026-09-03",
            end="2026-09-03",
            offset=0,
            limit=200,
            receipt_ids=[],
        )
        result = client.post("/api/explorer/query", json=selection)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["total"] == 3 and len(data["rows"]) == 3
        assert all(r["trade_time"].startswith("2026-09-03") for r in data["rows"])
        selection["receipt_ids"] = data["receipt_ids"]
        assert client.post("/api/explorer/export", json=selection).status_code == 403
        # Browsing remains available without granting the separate export permission.
        assert client.post("/api/explorer/query", json=selection).json()["rows"] == data["rows"]
        version = data["versions"][0]
        from northstar_quant.data_management.tushare.publication import storage

        files = storage()
        path = files._path(version["parquet_hash"])
        path.write_bytes(b"corrupt")
        refused = client.post("/api/explorer/query", json=selection)
        assert refused.status_code == 422, refused.text
