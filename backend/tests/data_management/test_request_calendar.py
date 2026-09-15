"""Calendar-driven collection preserves night envelopes and never guesses closed days."""

import json
from datetime import date

import pytest
from sqlalchemy import text

from northstar_quant.data_management.tushare import planning, quality
from northstar_quant.data_management.tushare.request_calendar import load
from northstar_quant.data_management.tushare.scheduling import choose
from tests.data_management.test_tushare import automatic as automatic
from tests.data_management.test_tushare import calendar_for_planning


def candidate(library):
    with library._engine.begin() as c:
        c.execute(text("DELETE FROM data_contract_collections"))
        c.execute(text("DELETE FROM data_sync_contracts"))
        c.execute(
            text("""INSERT INTO data_sync_contracts
            (ts_code,exchange,product,kind,details) VALUES
            ('AL1501.SHF','SHFE','AL','1',
            '{"list_date":"20141229","delist_date":"20150106",
              "last_ddate":"20150107","d_mode_desc":"实物交割"}')""")
        )


def test_calendar_arrival_unlocks_requests_with_friday_night_and_no_weekend_requests(automatic):
    candidate(automatic)
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        assert set(c.scalars(text("SELECT dataset FROM data_sync_jobs"))) == {"calendar"}
        assert choose(c, download_ready=True)["dataset"] == "calendar"
    calendar_for_planning(automatic, start="2013-01-01", end="2015-12-31")
    with automatic._engine.begin() as c:
        c.execute(text("DELETE FROM data_sync_calendar WHERE cal_date='2014-12-28'"))
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        assert set(c.scalars(text("SELECT dataset FROM data_sync_jobs"))) == {"calendar"}
    with automatic._engine.begin() as c:
        c.execute(text("INSERT INTO data_sync_calendar VALUES('SHFE','2014-12-28',false)"))
        c.execute(text("UPDATE data_sync_calendar SET is_open=false WHERE cal_date='2015-01-01'"))
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        rows = (
            c.execute(text("SELECT * FROM data_sync_jobs WHERE dataset='1min' ORDER BY start_at"))
            .mappings()
            .all()
        )
        assert rows[0]["start_at"] == "2014-12-29"
        assert rows[0]["parameters"]["start_date"] == "2014-12-26 00:00:00"
        assert rows[1]["parameters"]["start_date"] == "2014-12-31 00:00:00"
        daily = (
            c.execute(
                text("SELECT * FROM data_sync_jobs WHERE dataset='daily' AND start_at='2015-01-01'")
            )
            .mappings()
            .one()
        )
        assert daily["parameters"]["start_date"] == "20150102"
        calendar = load(c, "SHFE", date(2014, 12, 29), date(2015, 1, 6))
        assert calendar is not None
        count = c.scalar(text("SELECT count(*) FROM data_sync_jobs"))
        contract = c.execute(text("SELECT * FROM data_sync_contracts")).mappings().one()
        planning._window(
            c,
            "1min",
            contract,
            date(2015, 1, 3),
            date(2015, 1, 4),
            owner="AL1501.SHF",
            calendar=calendar,
        )
        assert c.scalar(text("SELECT count(*) FROM data_sync_jobs")) == count


def test_minute_split_preserves_actual_night_bounds_and_rejects_outside_rows(automatic):
    candidate(automatic)
    calendar_for_planning(automatic, start="2013-01-01", end="2015-12-31")
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        job = dict(
            c.execute(
                text("SELECT * FROM data_sync_jobs WHERE dataset='1min' ORDER BY start_at LIMIT 1")
            )
            .mappings()
            .one()
        )
        assert planning.split(c, job)
        children = (
            c.execute(
                text("SELECT * FROM data_sync_jobs WHERE dataset='1min' AND end_at='2014-12-30'")
            )
            .mappings()
            .all()
        )
        assert children[0]["parameters"]["start_date"] == "2014-12-26 00:00:00"
    fields = ["ts_code", "trade_time", "open", "close", "high", "low", "vol", "amount", "oi"]

    def raw(clock):
        return json.dumps(
            dict(
                code=0,
                data=dict(fields=fields, items=[["AL1501.SHF", clock, 1, 1, 1, 1, 1, 10, 2]]),
            )
        ).encode()

    rows, evidence = quality.normalize(raw("2014-12-26 21:01:00"), job)
    assert len(rows) == 1 and evidence["excluded_rows"] == 0
    with pytest.raises(quality.InvalidResponse, match="超出请求窗口"):
        quality.normalize(raw("2014-12-25 21:01:00"), job)


def test_old_queued_contract_cannot_bypass_2015_floor(automatic):
    candidate(automatic)
    with automatic._engine.begin() as c:
        c.execute(
            text("""INSERT INTO data_sync_contracts
        (ts_code,exchange,product,kind,details,planned_revision)
        VALUES('AL1401.SHF','SHFE','AL','1',
        '{"list_date":"20130101","delist_date":"20140115","last_ddate":"20140120"}',1)""")
        )
        c.execute(
            text(
                "INSERT INTO data_contract_collections(scope,start_date,end_date) "
                "VALUES('AL1401.SHF','2013-01-01','2014-01-15')"
            )
        )
        planning.enqueue(c, "daily", "AL1401.SHF", {}, "2013-01-01", "2013-01-31")
        assert choose(c, download_ready=True) is None
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        assert choose(c, download_ready=True)["dataset"] == "calendar"


def test_combine_keeps_supplier_end_instead_of_reintroducing_closed_dates(automatic):
    from northstar_quant.data_management.tushare.batching import combine

    with automatic._engine.begin() as c:
        for start, end, supplied_end in [
            ("2015-01-01", "2015-01-31", "2015-01-30"),
            ("2015-02-01", "2015-02-28", "2015-02-27"),
        ]:
            planning.enqueue(
                c,
                "60min",
                "RB2610.SHF",
                dict(
                    freq="60min",
                    start_date=f"{start} 00:00:00",
                    end_date=f"{supplied_end} 23:59:59",
                ),
                start,
                end,
            )
        selected = (
            c.execute(text("SELECT * FROM data_sync_jobs ORDER BY start_at LIMIT 1"))
            .mappings()
            .one()
        )
        combined = combine(c, selected)
        assert combined["end_at"] == "2015-02-28"
        assert combined["parameters"]["end_date"] == "2015-02-27 23:59:59"


def test_closed_calendar_gap_does_not_block_verified_daily_records(automatic, monkeypatch):
    from northstar_quant.data_management.tushare import acquisition, jobs
    from northstar_quant.data_management.tushare.contract_review import review
    from tests.data_management.test_contract_review import lifetime
    from tests.data_management.test_tushare import pending, ready, response

    lifetime(automatic, start="20260904", end="20260907")
    calendar_for_planning(automatic)
    for day in ["2026-09-04", "2026-09-07"]:
        ready(automatic)
        pending(automatic, start=day, end=day)
        data = json.loads(response())
        data["data"]["items"][0][1] = day.replace("-", "")
        monkeypatch.setattr(acquisition, "fetch", lambda *a: json.dumps(data).encode())
        assert jobs.process_next(automatic, plan=False)["status"] == "VALIDATED"
    result = review(automatic._engine, "RB2610.SHF")
    daily = next(r for r in result["requirements"] if r["dataset"] == "daily")
    assert daily["status"] == "VERIFIED"
    assert not result["admitted"]
