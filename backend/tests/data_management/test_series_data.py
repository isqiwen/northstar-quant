"""Independent series receipts cannot become executable contract publications."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from northstar_quant.data_management.catalog.snapshot_reading import query
from northstar_quant.data_management.publications import PublishedDatasets
from northstar_quant.data_management.series_data import catalog, planning, processing, retention
from northstar_quant.data_management.tushare import acquisition, jobs
from northstar_quant.data_management.tushare import planning as requests
from tests.data_management import test_tushare

automatic = test_tushare.automatic


def enqueue(library, dataset, scope, *, day="2026-09-01", exchange="", product=""):
    with library._engine.begin() as c:
        c.execute(
            text("""INSERT INTO data_series_collections
            (dataset,scope,exchange,product,name,start_date)
            VALUES(:dataset,:scope,:exchange,:product,:scope,:day) ON CONFLICT DO NOTHING"""),
            dict(dataset=dataset, scope=scope, exchange=exchange, product=product, day=day),
        )
        params = dict(start_date=day.replace("-", ""), end_date=day.replace("-", ""))
        params["ts_code"] = scope
        identity = requests.enqueue(c, dataset, scope, params, day, day)
        planning.link(c, dataset, scope, identity)


def fetch_rows(monkeypatch, rows):
    payload = json.dumps(
        dict(code=0, data=dict(fields=list(rows[0]), items=[list(r.values()) for r in rows]))
    ).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *_: payload)


def test_index_identity_fixed_read_and_exact_archive_restore(automatic, monkeypatch, tmp_path):
    rows = [
        dict(
            ts_code=code, trade_date="20260901", open=12, high=13, low=11, close=12, vol=3, amount=2
        )
        for code in ("CU.NH", "AL.NH")
    ]
    for row in rows:
        enqueue(automatic, "index", row["ts_code"])
        fetch_rows(monkeypatch, [row])
        test_tushare.ready(automatic)
        result = jobs.process_next(automatic, plan=False)
        assert result["status"] == "VALIDATED", result
        processed = processing.process_next(automatic._engine, automatic._files)
        assert processed["published"] == 1, processed
    assert processing.process_next(automatic._engine, automatic._files) is None
    items = catalog.query(automatic._engine, dataset="index")
    assert {r["scope"] for r in items["rows"]} == {"AL.NH", "CU.NH"}
    version = catalog.versions(automatic._engine, dataset="index", scope="CU.NH")["rows"][0]
    root = PublishedDatasets.from_environment().root
    domain = "market/futures/indices/daily"
    fixed = query(root, version["snapshot_id"], domain=domain, series="CU.NH")
    assert fixed["total"] == 1
    assert fixed["rows"][0]["turnover_cny"] == "2000.000000000000"
    with pytest.raises(ValueError, match="合约"):
        query(root, version["snapshot_id"], domain=domain, contract="CU")
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_contract_publications")) == 0
        assert retention.references(c)
        retention.restore(c, tmp_path / "restored", automatic._files)
        retention.verify_all(c, tmp_path / "restored")
    assert query(tmp_path / "restored", version["snapshot_id"], domain=domain) == fixed
    # Later supplier dates create a separate fixed version, never replace the old one.
    enqueue(automatic, "index", "CU.NH", day="2026-09-02")
    fetch_rows(monkeypatch, [{**rows[0], "trade_date": "20260902", "close": 13}])
    test_tushare.ready(automatic)
    assert jobs.process_next(automatic, plan=False)["status"] == "VALIDATED"
    assert processing.process_next(automatic._engine, automatic._files)["published"] == 1
    assert catalog.versions(automatic._engine, dataset="index", scope="CU.NH")["total"] == 2
    assert query(root, version["snapshot_id"], domain=domain) == fixed


def test_mapping_rejects_unknown_target_and_retries_fixed_receipt(automatic, monkeypatch):
    enqueue(automatic, "mapping", "RB.SHF", exchange="SHFE", product="RB")
    with automatic._engine.begin() as c:
        c.execute(
            text("""INSERT INTO data_sync_contracts VALUES
        ('RB.SHF','SHFE','RB','2','{}',NULL,1)""")
        )
    fetch_rows(
        monkeypatch, [dict(ts_code="RB.SHF", trade_date="20260901", mapping_ts_code="RB2609.SHF")]
    )
    result = jobs.process_next(automatic, plan=False)
    assert result["status"] == "VALIDATED", result
    result = processing.process_next(automatic._engine, automatic._files)
    assert result["published"] == 0 and "映射目标" in result["error"]
    assert catalog.query(automatic._engine)["rows"][0]["blocked"] == 1
    with automatic._engine.begin() as c:
        c.execute(
            text("""INSERT INTO data_sync_contracts VALUES
        ('RB2609.SHF','SHFE','RB','1','{}',NULL,1)""")
        )
    assert catalog.retry(automatic._engine, dataset="mapping", scope="RB.SHF") == dict(retried=1)
    assert processing.process_next(automatic._engine, automatic._files)["published"] == 1
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_sync_attempts")) == 1
        assert c.scalar(text("SELECT count(*) FROM data_contract_publications")) == 0


def test_series_plan_uses_discovered_identity_and_oldest_dates(automatic):
    with automatic._engine.begin() as c:
        c.execute(
            text("""INSERT INTO data_sync_contracts VALUES
        ('RB.SHF','SHFE','RB','2','{"list_date":"20090101"}',NULL,1)""")
        )
    test_tushare.calendar_for_planning(automatic, start="2014-01-01", end="2015-12-31")
    for _ in range(4):
        planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        rows = c.execute(text("SELECT * FROM data_series_collections")).mappings().all()
        assert {(r["dataset"], r["scope"]) for r in rows if r["dataset"] != "index"} == {
            ("continuous", "RB.SHF"),
            ("mapping", "RB.SHF"),
            ("adjusted", "RB.SHF"),
        }
        assert {str(r["start_date"]) for r in rows} == {"2015-01-01"}
        jobs = (
            c.execute(text("SELECT * FROM data_sync_jobs WHERE dataset<>'calendar'"))
            .mappings()
            .all()
        )
        assert {r["start_at"] for r in jobs} == {"2015-01-01"}
        assert all(r["parameters"].get("ts_code") == r["scope"] for r in jobs)
        assert "CU.NH" in {r["scope"] for r in rows if r["dataset"] == "index"}
        assert c.scalar(text("SELECT count(*) FROM data_contract_requests")) == 0


def test_request_lanes_alternate_and_split_preserves_series_owner(automatic):
    from northstar_quant.data_management.tushare.scheduling import choose

    test_tushare.pending(automatic)
    enqueue(automatic, "index", "CU.NH")
    with automatic._engine.begin() as c:
        first = choose(c, download_ready=True)
        c.execute(
            text("INSERT INTO data_sync_attempts(generation,request_id) VALUES(:id,:request)"),
            dict(id=uuid4(), request=first["request_id"]),
        )
        second = choose(c, download_ready=True)
        assert {first["dataset"], second["dataset"]} == {"daily", "index"}
        identity = requests.enqueue(
            c,
            "index",
            "CU.NH",
            dict(ts_code="CU.NH", start_date="20260902", end_date="20260905"),
            "2026-09-02",
            "2026-09-05",
        )
        planning.link(c, "index", "CU.NH", identity)
        parent = (
            c.execute(
                text("SELECT * FROM data_sync_jobs WHERE identity=:identity"),
                dict(identity=identity),
            )
            .mappings()
            .one()
        )
        assert requests.split(c, dict(parent))
        children = (
            c.execute(
                text("""SELECT j.* FROM data_sync_jobs j
        JOIN data_series_requests s USING(request_id) WHERE j.start_at>='2026-09-02'
        AND j.request_id<>:parent"""),
                dict(parent=parent["request_id"]),
            )
            .mappings()
            .all()
        )
        assert {(r["start_at"], r["end_at"]) for r in children} == {
            ("2026-09-02", "2026-09-03"),
            ("2026-09-04", "2026-09-05"),
        }
        assert c.scalar(text("SELECT count(*) FROM data_contract_requests")) == 1
