"""Pinned range reads, corruption refusal, coverage evidence and export authorization."""

import json
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from northstar_quant.apps.data_hub.application import create_app
from northstar_quant.data_management.exploration import catalog, discovery, quality, rows
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.storage_identity import initialize
from northstar_quant.data_management.tushare import acquisition, credentials, jobs, planning
from tests.apps.browser import login_response


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
    monkeypatch.setenv("NORTHSTAR_DATA_DIR", str(tmp_path / "source"))
    monkeypatch.delenv("NORTHSTAR_STORAGE_ID", raising=False)
    library = DataLibrary(engine, SourceFiles(tmp_path / "source", min_free_bytes=0))
    with engine.begin() as c:
        c.execute(
            text("""UPDATE data_sync_settings SET enabled=true,revision=1,
            refresh_at=now()+interval '1 day',api_next_at='{}',next_request_at=now()""")
        )
        c.execute(
            text("""INSERT INTO data_sync_contracts
            (ts_code,exchange,product,kind,details,planned_revision)
            VALUES ('RB2610.SHF','SHFE','RB','1',
            '{"list_date":"20200101","delist_date":"20260903"}',1)""")
        )
        c.execute(
            text("""INSERT INTO data_sync_calendar VALUES
            ('SHFE','2026-09-01',true),('SHFE','2026-09-02',false),('SHFE','2026-09-03',true)""")
        )
        c.execute(
            text("""INSERT INTO data_contract_collections(scope,start_date,end_date)
            VALUES('RB2610.SHF','2020-01-01','2026-09-03')""")
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
    process = jobs.process_next

    def completed_request(*args, **kwargs):
        result = process(*args, **kwargs)
        if result and result["status"] == "VALIDATED":
            publish_read_fixture(library)
        return result

    monkeypatch.setattr(jobs, "process_next", completed_request)
    assert jobs.process_next(library)["status"] == "VALIDATED"
    return library, response


def publish_read_fixture(library):
    # Synthetic already-admitted publication facts for reader/restore regression.
    # This does not exercise or establish supplier whole-contract admission.
    from northstar_quant.data_management.contract_data.packages import write_package
    from northstar_quant.data_management.publications import PublishedDatasets
    from northstar_quant.data_management.tushare.store import serial

    with library._engine.begin() as c:
        inputs = [
            serial(r)
            for r in c.execute(
                text("""SELECT r.*,j.dataset,j.scope
            FROM data_sync_jobs j JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
            WHERE j.status='VALIDATED' AND j.scope='RB2610.SHF'
            ORDER BY j.dataset,r.receipt_id""")
            ).mappings()
        ]
        if not inputs:
            return
        manifest = dict(
            rule="SYNTHETIC_READER_ACCEPTANCE",
            scope="RB2610.SHF",
            exchange="SHFE",
            product="RB",
            inputs=inputs,
        )
        artifact = write_package(
            PublishedDatasets.from_environment().root, manifest, library._files
        )
        c.execute(
            text("""INSERT INTO data_contract_publications
            (publication_id,scope,manifest,package_hash,package_bytes,path)
            VALUES(:id,'RB2610.SHF',CAST(:manifest AS jsonb),:hash,:bytes,:path)
            ON CONFLICT DO NOTHING"""),
            dict(
                id=artifact["publication_id"],
                manifest=json.dumps(manifest),
                hash=artifact["sha256"],
                bytes=artifact["bytes"],
                path=artifact["path"],
            ),
        )


def test_fixed_pages_and_revision_do_not_mix(published):
    library, response = published
    engine = library._engine
    args = dict(dataset="1min", scope="RB2610.SHF", start="2026-09-01", end="2026-09-03")
    first = rows.read(engine, **args, receipt_ids=[], limit=2)
    ids = [UUID(v) for v in first["receipt_ids"]]
    assert first["total"] == 6
    assert first["rows"][0]["open"] == "3100.1"
    assert not first["export_allowed"]
    assert next(f for f in first["fields"] if f["key"] == "oi")["missing"] == 6
    response["data"]["items"][0][2] = "3100.20"
    with engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    assert jobs.process_next(library)["status"] == "VALIDATED"
    latest = rows.read(engine, **args, receipt_ids=[])
    assert latest["view_id"] != first["view_id"]
    pages = [rows.read(engine, **args, receipt_ids=ids, offset=n, limit=2) for n in (0, 2, 4)]
    assert {p["view_id"] for p in pages} == {first["view_id"]}
    exact = [r for p in pages for r in p["rows"]]
    assert len({r["_key"] for r in exact}) == 6
    assert exact[0]["open"] == "3100.1"
    assert len(catalog.versions(engine, **args)["rows"]) == 2
    discovered = discovery.available(engine, "1min", "", "", "rb2610", 0)
    assert discovered["total"] == 1
    assert discovered["rows"][0]["receipt_id"] == latest["receipt_ids"][0]
    # A revision arriving after discovery cannot change the selected publication.
    reopened = discovery.open_published(engine, ids[0])
    assert reopened["rows"][0]["open"] == "3100.1"
    with pytest.raises(ValueError, match="冲突"):
        rows.read(engine, **args, receipt_ids=ids + [UUID(latest["receipt_ids"][0])])
    with pytest.raises(ValueError, match="不属于"):
        rows.read(engine, **{**args, "scope": "FAKE.SHF"}, receipt_ids=ids)


def test_coverage_never_infers_complete_minutes(published):
    library, _ = published
    args = dict(dataset="1min", scope="RB2610.SHF", start="2026-09-01", end="2026-09-04")
    report = quality.coverage(library._engine, **args)
    assert [r["state"] for r in report["days"]] == ["RESPONSE_VALIDATED"] * 3 + ["NOT_APPLICABLE"]
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
        csrf = login_response(client).json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        available = client.post(
            "/api/explorer/available",
            json=dict(dataset="", exchange="", product="", search="", offset=0),
        ).json()
        assert available["total"] == 1
        opened = client.post(
            "/api/explorer/open", json={"receipt_id": available["rows"][0]["receipt_id"]}
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["total"] == 6
        assert (opened.json()["start"], opened.json()["end"]) == ("2026-09-01", "2026-09-03")
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
        assert (
            client.post(
                "/api/explorer/open", json={"receipt_id": data["receipt_ids"][0]}
            ).status_code
            == 422
        )


def test_revision_comparison_pins_both_versions_and_distinguishes_null(published):
    from northstar_quant.data_management.exploration import revisions

    library, content = published
    engine = library._engine
    with engine.connect() as c:
        before = c.scalar(text("SELECT receipt_id FROM data_sync_receipts"))
    content["data"]["items"][0][2] = "3100.20"
    content["data"]["items"][0][-1] = "0"
    content["data"]["items"].pop()
    added = list(content["data"]["items"][-1])
    added[1] = "2026-09-03 09:04:00"
    content["data"]["items"].append(added)
    with engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    after = UUID(jobs.process_next(library)["receipt_id"])
    report = revisions.compare(engine, before_id=before, after_id=after)
    assert report["counts"] == dict(added=1, removed=1, changed=1, unchanged=4)
    assert report["source_changed"] and not report["rules_changed"]
    changed = [r for r in report["changes"] if r["kind"] == "changed"]
    assert next(r for r in changed if r["field"] == "open")["before"] == "3100.1"
    oi = next(r for r in changed if r["field"] == "oi")
    assert oi["before_present"] and oi["before"] is None and oi["after"] == "0"
    # New publication cannot alter the explicitly pinned comparison.
    content["data"]["items"][0][2] = "3100.30"
    with engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    jobs.process_next(library)
    assert revisions.compare(engine, before_id=before, after_id=after) == report
    reverse = revisions.compare(engine, before_id=after, after_id=before)
    assert reverse["comparison_id"] != report["comparison_id"]
    assert (
        next(r for r in reverse["changes"] if r["field"] == "open" and r["kind"] == "changed")[
            "after"
        ]
        == "3100.1"
    )
    from tests.apps.browser import ProtocolClient

    with ProtocolClient(create_app(engine, library), base_url="http://127.0.0.1") as client:
        csrf = login_response(client).json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        response = client.post(
            "/api/explorer/compare",
            json={"before_id": str(before), "after_id": str(after), "offset": 0},
        )
        assert response.status_code == 200, response.text
        assert response.json()["comparison_id"] == report["comparison_id"]
    with engine.connect() as c:
        receipt = (
            c.execute(text("SELECT * FROM data_sync_receipts WHERE receipt_id=:id"), {"id": before})
            .mappings()
            .one()
        )
    from northstar_quant.data_management.tushare import publication

    publication.storage()._path(receipt["parquet_hash"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        revisions.compare(engine, before_id=before, after_id=after)


def test_rule_only_revision_has_no_row_changes(published, monkeypatch):
    from northstar_quant.data_management.exploration import revisions
    from northstar_quant.data_management.tushare import quality as validation
    from northstar_quant.data_management.tushare import reprocessing

    library, _ = published
    with library._engine.connect() as c:
        first = c.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
        source = c.scalar(text("SELECT generation FROM data_sync_attempts"))
    monkeypatch.setattr(validation, "RULE", "test-different-quality-rule")
    reprocessing.enqueue(library._engine, request_id=first["request_id"], source_generation=source)
    after = UUID(jobs.process_next(library)["receipt_id"])
    report = revisions.compare(library._engine, before_id=first["receipt_id"], after_id=after)
    assert report["rules_changed"] and not report["source_changed"]
    assert report["total"] == 0 and report["counts"]["unchanged"] == 6
    with pytest.raises(ValueError):
        revisions.compare(library._engine, before_id=after, after_id=after)
    with pytest.raises(LookupError):
        revisions.compare(library._engine, before_id=UUID(int=1), after_id=after)


def test_published_range_scan_reports_pruning_without_changing_values(published):
    library, response = published
    template = response["data"]["items"][0]
    response["data"]["items"] = [
        [template[0], f"2026-09-0{day} {minute // 60:02d}:{minute % 60:02d}:00", *template[2:]]
        for day in (1, 3)
        for minute in range(512)
    ]
    with library._engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    assert jobs.process_next(library)["status"] == "VALIDATED"
    whole = rows.read(
        library._engine, "1min", "RB2610.SHF", "2026-09-01", "2026-09-03", [], limit=1000
    )
    narrow = rows.read(
        library._engine,
        "1min",
        "RB2610.SHF",
        "2026-09-01",
        "2026-09-01",
        [UUID(v) for v in whole["receipt_ids"]],
        limit=1000,
    )
    assert narrow["rows"] == whole["rows"][:512]
    assert narrow["scan"]["verified_bytes"] == whole["scan"]["verified_bytes"]
    assert narrow["scan"]["rows_decoded"] == 512
    assert whole["scan"]["rows_decoded"] == 1024
    assert narrow["scan"]["row_groups_read"] == 1
    assert whole["scan"]["row_groups_read"] == 2


def test_available_excludes_unpublished_material_and_keeps_fixed_split_inputs(published):
    library, _ = published
    engine = library._engine
    with engine.begin() as c:
        c.execute(
            text("""INSERT INTO data_sync_contracts
            (ts_code,exchange,product,kind,details,planned_revision)
            VALUES ('A0801.DCE','DCE','A','1','{}',1)""")
        )
        planning.enqueue(
            c, "15min", "A0801.DCE", {"ts_code": "A0801.DCE"}, "2026-09-01", "2026-09-03"
        )
    assert discovery.available(engine, "", "", "", "A0801", 0)["total"] == 0
    assert discovery.available(engine, "15min", "", "", "", 0)["total"] == 0
    assert discovery.available(engine, "", "DCE", "", "", 0)["total"] == 0
    found = discovery.available(engine, "", "SHFE", "RB", "rb", 0)
    assert found["total"] == 1 and found["rows"][0]["row_count"] == 6
    with engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='SPLIT' WHERE scope='RB2610.SHF'"))
    assert discovery.available(engine, "", "", "", "", 0)["total"] == 1


def test_named_instrument_and_full_chart_share_fixed_rows(published):
    from tests.apps.browser import ProtocolClient

    library, response = published
    engine = library._engine
    template = response["data"]["items"][0]
    response["data"]["items"] = [
        [
            template[0],
            (datetime(2026, 9, 1, 9) + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            *template[2:],
        ]
        for i in range(250)
    ]
    with engine.begin() as c:
        c.execute(
            text("UPDATE data_sync_contracts SET details=details||CAST(:name AS jsonb)"),
            {"name": json.dumps({"name": "螺纹钢2610"})},
        )
        c.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    assert jobs.process_next(library)["status"] == "VALIDATED"
    with ProtocolClient(create_app(engine, library), base_url="http://127.0.0.1") as client:
        csrf = login_response(client).json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        search = dict(dataset="", exchange="", product="", search="螺纹钢", offset=0)
        found = client.post("/api/explorer/available", json=search).json()
        assert found["total"] == 1 and found["rows"][0]["scope"] == "RB2610.SHF"
        assert found["rows"][0]["display_name"] == "螺纹钢2610"
        opened = client.post(
            "/api/explorer/select", json={"scope": "RB2610.SHF", "dataset": "1min"}
        )
        assert opened.status_code == 200 and opened.json()["total"] == 250
        instrument = client.post("/api/explorer/instrument", json={"scope": "RB2610.SHF"}).json()
        assert instrument["name"] == "螺纹钢2610" and instrument["periods"] == ["1min"]
        selection = dict(
            dataset="1min",
            scope="RB2610.SHF",
            start="2026-09-01",
            end="2026-09-03",
            receipt_ids=[found["rows"][0]["receipt_id"]],
        )
        page = client.post(
            "/api/explorer/query", json={**selection, "offset": 0, "limit": 200}
        ).json()
        chart = client.post("/api/explorer/chart", json=selection)
        assert chart.status_code == 200, chart.text
        data = chart.json()
        assert data["view_id"] == page["view_id"] and len(data["rows"]) == 250
        assert data["rows"][:200] == page["rows"]
        assert (
            client.post("/api/explorer/chart", json={**selection, "receipt_ids": []}).status_code
            == 422
        )
        assert (
            client.post("/api/explorer/chart", json={**selection, "scope": "OTHER.SHF"}).status_code
            == 422
        )


def test_contract_discovery_groups_periods_and_selects_populated_native_range(published):
    library, response = published
    engine = library._engine
    with engine.begin() as c:
        planning.enqueue(
            c, "15min", "RB2610.SHF", {"ts_code": "RB2610.SHF"}, "2026-09-01", "2026-09-03"
        )
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    assert jobs.process_next(library)["status"] == "VALIDATED"
    found = discovery.available(engine, "", "", "", "", 0)
    assert found["total"] == 1
    assert set(found["rows"][0]["periods"]) == {"1min", "15min"}
    assert found["rows"][0]["publications"] == 2
    first = discovery.open_instrument(engine, "RB2610.SHF", "1min")
    other = discovery.open_instrument(engine, "RB2610.SHF", "15min")
    assert first["dataset"] == "1min" and other["dataset"] == "15min"
    assert first["total"] == other["total"] == 6
    assert first["receipt_ids"] != other["receipt_ids"]
    assert first["start"] == "2026-09-01" and first["end"] == "2026-09-03"
    with pytest.raises(ValueError, match="尚无"):
        discovery.open_instrument(engine, "RB2610.SHF", "daily")
    with pytest.raises(ValueError, match="尚无"):
        discovery.open_instrument(engine, "RB2611.SHF", "1min")


def test_ordinary_reader_rejects_missing_or_modified_contract_package(published):
    from northstar_quant.data_management.contract_data.reading import query
    from northstar_quant.data_management.publications import PublishedDatasets

    library, _ = published
    values = dict(
        dataset="1min", scope="RB2610.SHF", start="2026-09-01", end="2026-09-03", receipt_ids=[]
    )
    assert query(library._engine, **values)["total"] == 6
    with library._engine.connect() as c:
        path = c.scalar(
            text("SELECT path FROM data_contract_publications ORDER BY created_at DESC LIMIT 1")
        )
    package = PublishedDatasets.from_environment().root / path
    content = package.read_bytes()
    package.write_bytes(b"x" * len(content))
    with pytest.raises(ValueError, match="完整性检查失败"):
        query(library._engine, **values)
    package.unlink()
    with pytest.raises(ValueError, match="发布包丢失"):
        query(library._engine, **values)
