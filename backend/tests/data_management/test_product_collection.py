"""Product selection and historical preflight cannot grant incomplete publications."""

import json
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text

from northstar_quant.data_management.tushare import planning, products, settings
from northstar_quant.data_management.tushare.scheduling import choose
from tests.data_management.test_request_calendar import candidate
from tests.data_management.test_tushare import automatic as automatic
from tests.data_management.test_tushare import calendar_for_planning, pending


def prepare(library):
    candidate(library)
    calendar_for_planning(library, start="2013-01-01", end="2015-12-31")
    planning.plan(library._engine)


def test_empty_selection_and_switch_do_not_run_old_queue(automatic):
    pending(automatic)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_settings SET selected_products='{}'"))
        assert choose(c, download_ready=True) is None
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        assert c.scalar(text("SELECT count(*) FROM data_sync_jobs")) == 1
        planning.enqueue(c, "contracts", "SHFE", {"exchange": "SHFE", "fut_type": "1"}, "", "")
        assert choose(c, download_ready=True)["dataset"] == "contracts"
        c.execute(text("DELETE FROM data_sync_jobs WHERE dataset='contracts'"))
        products.validate(c, [])
        with pytest.raises(ValueError, match="品种选择无效"):
            products.validate(c, ["SHFE:UNKNOWN"])
    state = settings.configure(automatic._engine, revision=1, enabled=True, products=["SHFE:RB"])
    assert state["settings"]["selected_products"] == ["SHFE:RB"]
    with automatic._engine.begin() as c:
        assert choose(c, download_ready=True)["scope"] == "RB2610.SHF"
    with pytest.raises(ValueError, match="另一页面"):
        settings.configure(automatic._engine, revision=1, enabled=True, products=[])
    assert settings.status(automatic._engine)["settings"]["selected_products"] == ["SHFE:RB"]


def test_preflight_reuses_requests_then_expands_untruncated_lifetime(automatic):
    prepare(automatic)
    with automatic._engine.begin() as c:
        first = set(
            c.scalars(text("SELECT identity FROM data_sync_jobs WHERE dataset<>'calendar'"))
        )
        assert first
        assert (
            c.scalar(text("SELECT max(end_at) FROM data_sync_jobs WHERE dataset<>'calendar'"))
            == "2014-12-31"
        )
        assert not c.scalar(text("SELECT discovery_complete FROM data_contract_collections"))
        # Synthetic successful opening responses; final whole-contract review is separate.
        c.execute(text("UPDATE data_sync_jobs SET status='VALIDATED'"))
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        assert c.scalar(text("SELECT discovery_complete FROM data_contract_collections"))
        all_ids = set(c.scalars(text("SELECT identity FROM data_sync_jobs")))
        assert first < all_ids
        assert (
            c.scalar(text("SELECT max(end_at) FROM data_sync_jobs WHERE dataset='1min'"))
            == "2015-01-06"
        )
        assert (
            c.scalar(text("SELECT start_date::text FROM data_contract_collections")) == "2014-12-29"
        )
        assert c.scalar(text("SELECT count(*) FROM data_contract_publications")) == 0
        count = len(all_ids)
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_sync_jobs")) == count


@pytest.mark.parametrize(
    "failure,skipped",
    [("empty", True), ("invalid", True), ("permission", False), ("network", False)],
)
def test_preflight_distinguishes_supplier_records_from_request_failures(
    automatic, failure, skipped
):
    prepare(automatic)
    with automatic._engine.begin() as c:
        request = c.scalar(text("SELECT request_id FROM data_sync_jobs WHERE dataset='daily'"))
        generation = uuid4()
        issue = {"issues": [{"reason": "OHLC"}]} if failure == "invalid" else {}
        error = {
            "empty": "历史区间返回空数据，7 天后复核覆盖；不认定已完成",
            "invalid": "OHLC 高低价关系不成立",
            "permission": "Tushare 权限不足",
            "network": "下载连接超时",
        }[failure]
        status = "WAITING" if failure in ("empty", "network") else "BLOCKED"
        c.execute(
            text("""INSERT INTO data_sync_attempts(generation,request_id,quality,outcome)
            VALUES(:g,:r,CAST(:q AS jsonb),:s)"""),
            dict(g=generation, r=request, q=json.dumps(issue), s=status),
        )
        c.execute(
            text("UPDATE data_sync_jobs SET status=:s,error=:e,generation=:g WHERE request_id=:r"),
            dict(s=status, e=error, g=generation, r=request),
        )
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        assert (
            c.scalar(text("SELECT status FROM data_contract_collections")) == "REJECTED"
        ) is skipped
        assert not c.scalar(text("SELECT discovery_complete FROM data_contract_collections"))
        assert c.scalar(text("SELECT count(*) FROM data_contract_publications")) == 0
        if skipped:
            products.retry(c, ["SHFE:AL"])
            assert c.scalar(text("SELECT status FROM data_contract_collections")) == "COLLECTING"
            assert (
                c.scalar(
                    text("SELECT status FROM data_sync_jobs WHERE request_id=:r"), {"r": request}
                )
                == "PENDING"
            )
            assert c.scalar(text("SELECT count(*) FROM data_sync_attempts")) == 1


def test_observed_origins_keep_each_dataset_and_contract_identity(automatic):
    prepare(automatic)
    with automatic._engine.begin() as c:
        for dataset, first in [("daily", "2014-12-29"), ("1min", "2014-12-30")]:
            request = c.scalar(
                text("SELECT request_id FROM data_sync_jobs WHERE dataset=:d"), {"d": dataset}
            )
            c.execute(
                text("""INSERT INTO data_sync_attempts(generation,request_id,quality,outcome)
                VALUES(:g,:r,CAST(:q AS jsonb),'VALIDATED')"""),
                dict(g=uuid4(), r=request, q=json.dumps({"first_observed": first})),
            )
        found = products.origins(c)
        assert {r["dataset"]: r["first_observed"] for r in found} == {
            "daily": "2014-12-29",
            "1min": "2014-12-30",
        }
        assert all(r["product"] == "AL" and not r["complete_history"] for r in found)
        c.execute(text("UPDATE data_sync_settings SET selected_products='{}'"))
        assert products.origins(c) == []


@pytest.mark.parametrize("exchange,product", [("SHFE", "AL"), ("DCE", "C"), ("CZCE", "MA")])
def test_listing_floor_filters_planning_and_existing_queue(
    automatic, monkeypatch, exchange, product
):
    monkeypatch.setattr(products, "LISTING_START", date(2025, 1, 1))
    with automatic._engine.begin() as c:
        c.execute(text("DELETE FROM data_contract_collections"))
        c.execute(text("DELETE FROM data_sync_contracts"))
        c.execute(
            text("UPDATE data_sync_settings SET selected_products=ARRAY[:p]"),
            {"p": f"{exchange}:{product}"},
        )
        for scope, listing, end in [
            ("OLD", "20241231", "20250601"),
            ("BOUNDARY", "20250101", "20250601"),
            ("ACTIVE", "20250102", "20990601"),
        ]:
            c.execute(
                text("""INSERT INTO data_sync_contracts
                (ts_code,exchange,product,kind,details) VALUES(:s,:e,:p,'1',CAST(:d AS jsonb))"""),
                dict(
                    s=scope,
                    e=exchange,
                    p=product,
                    d=json.dumps(
                        dict(
                            list_date=listing,
                            delist_date=end,
                            last_ddate=end,
                            d_mode_desc="实物交割",
                        )
                    ),
                ),
            )
        c.execute(
            text("""INSERT INTO data_contract_collections(scope,start_date,end_date)
            VALUES('OLD','2024-12-31','2025-06-01')""")
        )
        planning.enqueue(
            c,
            "daily",
            "OLD",
            dict(ts_code="OLD", start_date="20250102", end_date="20250102"),
            "2025-01-02",
            "2025-01-02",
        )
        assert choose(c, download_ready=True) is None
    calendar_for_planning(automatic, exchange, start="2024-01-01", end="2025-12-31")
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        assert set(c.scalars(text("SELECT scope FROM data_contract_collections"))) == {
            "OLD",
            "BOUNDARY",
        }
        assert (
            str(
                c.scalar(
                    text("SELECT start_date FROM data_contract_collections WHERE scope='BOUNDARY'")
                )
            )
            == "2025-01-01"
        )
        job = choose(c, download_ready=True)
        assert job is not None
        assert job["scope"] != "OLD"
        assert not c.scalar(
            text("SELECT EXISTS(SELECT 1 FROM data_sync_jobs WHERE scope='ACTIVE')")
        )
