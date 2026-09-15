"""Native report clocks must survive collection, review and fixed evidence."""

import json
from datetime import date

import pytest
from sqlalchemy import text

from northstar_quant.data_management.tushare import acquisition, jobs, origins, planning
from northstar_quant.data_management.tushare.contract_review import review
from tests.data_management import test_tushare
from tests.data_management.test_contract_review import lifetime

automatic = test_tushare.automatic


def collect(library, monkeypatch, rows, response_status="VALIDATED"):
    lifetime(library)
    with library._engine.begin() as c:
        contract = c.execute(text("SELECT * FROM data_sync_contracts")).mappings().one()
        planning._window(
            c, "weekly_detail", contract, date(2026, 1, 1), date(2026, 12, 31), owner="RB2610.SHF"
        )
    fields = list(rows[0])
    raw = json.dumps(
        dict(code=0, data=dict(fields=fields, items=[[r.get(f) for f in fields] for r in rows]))
    ).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *a: raw)
    assert jobs.process_next(library)["status"] == response_status
    return {r["dataset"]: r for r in review(library._engine, "RB2610.SHF")["requirements"]}[
        "weekly_detail"
    ]


def report(week="202637", day="20260904", volume=100):
    return dict(
        exchange="SHFE",
        prd="RB",
        week=week,
        week_date=day,
        vol=volume,
        amount=1,
        open_interest=20,
        cumvol=1000,
        cumamt=10,
    )


def test_native_week_label_does_not_move_actual_report_date(automatic, monkeypatch):
    item = collect(automatic, monkeypatch, [report()])
    assert item["status"] == "VERIFIED"
    assert item["evidence"]["actual_periods"] == 1
    with automatic._engine.connect() as c:
        assert origins.first(c, "weekly_detail", "SHFE:RB") == date(2026, 9, 4)
        job = c.execute(text("SELECT * FROM data_sync_jobs")).mappings().one()
        assert job["parameters"]["start_week"] == "202600"
        assert job["parameters"]["end_week"] == "202799"
        assert job["start_at"] == "2026-01-01"
        assert job["end_at"] == "2026-12-31"
        assert not planning.split(c, dict(job))
    # A product report alone must never publish the whole contract.
    assert not review(automatic._engine, "RB2610.SHF")["admitted"]


@pytest.mark.parametrize(
    "rows,status",
    [
        ([report(day=None)], "UNKNOWN"),
        ([report(day="20260828")], "INVALID"),
        ([report(), report(week="202636", volume=99)], "INVALID"),
        ([report(volume=None)], "INVALID"),
    ],
)
def test_missing_ambiguous_and_conflicting_reports_do_not_pass(
    automatic, monkeypatch, rows, status
):
    item = collect(automatic, monkeypatch, rows)
    assert item["status"] == status
    if rows[0]["week_date"] is None:
        assert item["evidence"]["undated_native_periods"] == ["202637"]
        with automatic._engine.connect() as c:
            assert origins.first(c, "weekly_detail", "SHFE:RB") is None


def test_weekly_calendar_holiday_accepts_actual_last_trading_day(automatic, monkeypatch):
    item = collect(automatic, monkeypatch, [report(day="20260902")])
    assert item["status"] == "VERIFIED"


@pytest.mark.parametrize("row", [report(day="20260230"), {**report(), "prd": "CU"}])
def test_invalid_weekly_date_or_product_is_retained_as_response_failure(
    automatic, monkeypatch, row
):
    item = collect(automatic, monkeypatch, [row], response_status="BLOCKED")
    assert item["status"] == "INVALID"
    with automatic._engine.connect() as c:
        attempt = c.execute(text("SELECT source_hash,quality FROM data_sync_attempts")).one()
        assert attempt.source_hash
        assert attempt.quality["issue_count"] == 1
