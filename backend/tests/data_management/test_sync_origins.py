"""Start discovery never converts absence into coverage or mixes frequencies."""

from datetime import date
from uuid import uuid4

from sqlalchemy import text

from northstar_quant.data_management.tushare import origins, planning, store
from tests.data_management.test_tushare import automatic as automatic


def test_origin_keeps_earliest_evidence_across_restarts_and_frequencies(automatic):
    engine = automatic._engine
    with engine.begin() as c:
        planning.enqueue(c, "index", "LR.NH", {}, "2012-01-01", "2014-12-31")
        request = c.scalar(text("SELECT request_id FROM data_sync_jobs"))
        generation = uuid4()
        c.execute(
            text("""INSERT INTO data_sync_attempts(generation,request_id,source_hash)
            VALUES(:g,:r,'retained-response')"""),
            {"g": generation, "r": request},
        )
        job = {
            "dataset": "index",
            "scope": "LR.NH",
            "generation": generation,
            "request_id": request,
        }
        origins.observe(c, job, [])
        assert origins.first(c, "index", "LR.NH") is None
        origins.observe(c, job, [{"trade_date": "20140801"}])
        second = uuid4()
        c.execute(
            text("""INSERT INTO data_sync_attempts(generation,request_id,source_hash)
            VALUES(:g,:r,'earlier-response')"""),
            {"g": second, "r": request},
        )
        origins.observe(c, {**job, "generation": second}, [{"trade_date": "20140708"}])
        # A subsequent observation cannot move the retained earliest date forward.
        origins.observe(c, job, [{"trade_date": "20140901"}])
        assert origins.first(c, "index", "LR.NH") == date(2014, 7, 8)
        assert origins.first(c, "1min", "LR.NH") is None
    evidence = store.job(engine, request)["origin"]
    assert evidence["first_observed"] == "2014-07-08"
    assert evidence["source_hash"] == "earlier-response"
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0


def test_internal_requests_preserve_pre_2012_lifetime(automatic):
    with automatic._engine.begin() as c:
        planning.enqueue(
            c,
            "1min",
            "RB2610.SHF",
            {"start_date": "2011-12-01 00:00:00"},
            "2011-12-01",
            "2012-01-05",
        )
        job = c.execute(text("SELECT * FROM data_sync_jobs")).mappings().one()
        assert job["start_at"] == "2011-12-01"
        planning.split(c, dict(job))
        assert c.scalar(text("SELECT min(start_at) FROM data_sync_jobs")) == "2011-12-01"
        assert c.scalar(text("SELECT count(*) FROM data_contract_requests")) == 3


def test_actual_contract_missing_listing_never_borrows_product_start(automatic, monkeypatch):
    monkeypatch.setattr(planning, "target_day", lambda: date(2026, 9, 9))
    with automatic._engine.begin() as c:
        c.execute(
            text(
                "UPDATE data_sync_contracts SET planned_revision=0, "
                "details=jsonb_build_object('delist_date','20260901')"
            )
        )
        c.execute(
            text("""INSERT INTO data_sync_contracts
            (ts_code,exchange,product,kind,details,planned_revision)
            VALUES('RB2611.SHF','SHFE','RB','1','{"list_date":"20250101"}',1)""")
        )
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        assert c.scalar(
            text("SELECT planning_error FROM data_sync_contracts WHERE ts_code='RB2610.SHF'")
        )
        assert c.scalar(text("SELECT count(*) FROM data_sync_jobs WHERE scope='RB2610.SHF'")) == 0
