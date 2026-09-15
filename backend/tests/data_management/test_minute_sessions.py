"""Night attribution, holiday auctions and missing bars affect actual admission."""

import json
from datetime import date, datetime

import pytest
from sqlalchemy import text

from northstar_quant.data_management.contract_data.minute_review import inspect
from northstar_quant.data_management.contract_data.minute_sessions import Sessions
from northstar_quant.data_management.tushare import acquisition, jobs, planning
from tests.data_management.test_tushare import automatic as automatic
from tests.data_management.test_tushare import calendar_for_planning


def test_friday_night_belongs_to_monday_and_holiday_has_day_auction():
    s = Sessions(
        (date(2025, 1, 17), date(2025, 1, 20)), "calendar", date(2025, 1, 20), date(2025, 1, 20)
    )
    labels = s.labels("30min")
    assert labels["2025-01-17 21:00:00"] == date(2025, 1, 20)
    assert s.trading_day(datetime(2025, 1, 17, 22)) == date(2025, 1, 20)
    assert "2025-01-20 10:45:00" in labels
    assert "2025-01-20 10:30:00" not in labels
    assert "2025-01-20 15:00:00" in labels
    assert "2025-01-20 09:00:00" not in labels
    h = Sessions(
        (date(2025, 1, 27), date(2025, 2, 5)), "calendar", date(2025, 2, 5), date(2025, 2, 5)
    )
    assert "2025-01-27 21:00:00" not in h.labels("60min")
    assert "2025-02-05 09:00:00" in h.labels("60min")
    assert "2025-02-05 14:15:00" in h.labels("60min")


@pytest.mark.parametrize("missing", [False, True])
def test_owned_minute_rows_require_every_label_including_listing_eve(
    automatic, monkeypatch, missing
):
    e = automatic._engine
    with e.begin() as c:
        c.execute(text("DELETE FROM data_contract_collections"))
        c.execute(text("DELETE FROM data_sync_contracts"))
        c.execute(text("UPDATE data_sync_settings SET selected_products=ARRAY['DCE:C']"))
        c.execute(
            text("""INSERT INTO data_sync_contracts
          (ts_code,exchange,product,kind,details,planned_revision)
          VALUES('C2505.DCE','DCE','C','1','{"list_date":"20250116","delist_date":"20250117","last_ddate":"20250120"}',1)""")
        )
        c.execute(
            text("""INSERT INTO data_contract_collections
          (scope,start_date,end_date,discovery_complete)
          VALUES('C2505.DCE','2025-01-16','2025-01-17',true)""")
        )
        planning.enqueue(
            c,
            "5min",
            "C2505.DCE",
            dict(
                ts_code="C2505.DCE",
                freq="5min",
                start_date="2025-01-15 00:00:00",
                end_date="2025-01-17 23:59:59",
            ),
            "2025-01-16",
            "2025-01-17",
        )
    calendar_for_planning(automatic, "DCE", "2024-01-01", "2025-01-17")
    s = Sessions(
        (date(2025, 1, 15), date(2025, 1, 16), date(2025, 1, 17)),
        "synthetic",
        date(2025, 1, 16),
        date(2025, 1, 17),
    )
    labels = sorted(s.labels("5min"))
    if missing:
        labels.remove("2025-01-15 21:00:00")
    body = json.dumps(
        dict(
            code=0,
            data=dict(
                fields=[
                    "ts_code",
                    "trade_time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "vol",
                    "amount",
                    "oi",
                ],
                items=[["C2505.DCE", t, 100, 101, 99, 100, 0, 0, 0] for t in labels],
            ),
        )
    ).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *a, **kw: body)
    result = jobs.process_next(automatic)
    assert result["status"] == "VALIDATED", (result["dataset"], result["error"])
    with e.connect() as c:
        result = inspect(c, "C2505.DCE", "DCE", "5min", s.start, s.end, product="C")
        assert result["evidence"]["grid_verified"] is (not missing)
        assert result["status"] == ("RECEIVED" if missing else "VERIFIED")
        if missing:
            assert result["evidence"]["missing_labels"] == ["2025-01-15 21:00:00"]
        assert not inspect(c, "C2505.DCE", "DCE", "5min", s.start, s.end, product="M")["evidence"][
            "grid_verified"
        ]
