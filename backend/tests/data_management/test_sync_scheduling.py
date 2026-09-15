"""Durable initial boundaries, fair history selection and truthful lane progress."""

from uuid import uuid4

from sqlalchemy import text

from northstar_quant.data_management.tushare import planning, scheduling, settings
from tests.data_management.test_tushare import automatic as automatic


def add(connection, dataset, day, scope="RB2610.SHF"):
    planning.enqueue(connection, dataset, scope, {"start_date": day}, day, day)
    return connection.scalar(
        text("SELECT request_id FROM data_sync_jobs WHERE dataset=:d AND start_at=:s AND scope=:c"),
        {"d": dataset, "s": day, "c": scope},
    )


def attempt(connection, request_id):
    connection.execute(
        text(
            "INSERT INTO data_sync_attempts(generation,request_id,outcome,finished_at) "
            "VALUES(:g,:r,'EMPTY',now())"
        ),
        {"g": uuid4(), "r": request_id},
    )
    connection.execute(
        text(
            "UPDATE data_sync_jobs SET status='WAITING',next_at=now()+interval '7 days' "
            "WHERE request_id=:r"
        ),
        {"r": request_id},
    )


def test_requests_without_a_retired_contract_owner_cannot_run(automatic):
    with automatic._engine.begin() as c:
        add(c, "daily", "2012-01-01", "UNOWNED")
        owned = add(c, "1min", "2012-02-01")
        assert scheduling.choose(c, download_ready=True)["request_id"] == owned
        attempt(c, owned)
        assert scheduling.choose(c, download_ready=True) is None


def test_contract_requests_resume_after_restart_and_respect_quota(automatic):
    with automatic._engine.begin() as c:
        old = add(c, "daily", "2012-01-01")
        new = add(c, "daily", "2012-02-01")
        c.execute(text("UPDATE data_sync_settings SET revision=revision+1"))
    with automatic._engine.begin() as c:
        assert scheduling.choose(c, download_ready=True)["request_id"] == old
        attempt(c, old)
        assert scheduling.choose(c, download_ready=True)["request_id"] == new
        assert scheduling.choose(c, download_ready=False) is None


def test_response_success_does_not_increment_published_contracts(automatic):
    with automatic._engine.begin() as c:
        add(c, "daily", "2012-01-01")
        c.execute(text("UPDATE data_sync_jobs SET status='VALIDATED'"))
    result = settings.status(automatic._engine)
    lane = result["lanes"][0]
    assert lane["lane"] == "contracts"
    assert lane["total"] == 1
    assert lane["validated"] == 0


def test_api_cooldown_is_shared_by_native_periods_but_not_other_apis(automatic):
    import json
    from datetime import UTC, datetime, timedelta

    with automatic._engine.begin() as c:
        add(c, "1min", "2012-01-01")
        add(c, "15min", "2012-01-01")
        daily = add(c, "daily", "2012-01-01")
        c.execute(
            text("UPDATE data_sync_settings SET api_next_at=CAST(:state AS jsonb)"),
            {
                "state": json.dumps(
                    {"ft_mins": (datetime.now(UTC) + timedelta(minutes=2)).isoformat()}
                )
            },
        )
    # Cooldown survives another transaction / worker, and cannot be bypassed by changing freq.
    with automatic._engine.begin() as c:
        assert scheduling.choose(c, download_ready=True)["request_id"] == daily
        attempt(c, daily)
        assert scheduling.choose(c, download_ready=True) is None
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}'"))
        assert scheduling.choose(c, download_ready=True)["dataset"] in ("1min", "15min")


def test_large_native_request_preserves_children_and_truncation_can_recover(automatic):
    from northstar_quant.data_management.tushare import batching

    with automatic._engine.begin() as c:
        for start, end in [("2012-01-01", "2012-01-31"), ("2012-02-01", "2012-02-29")]:
            planning.enqueue(
                c,
                "15min",
                "RB2610.SHF",
                {
                    "ts_code": "RB2610.SHF",
                    "freq": "15min",
                    "start_date": f"{start} 00:00:00",
                    "end_date": f"{end} 23:59:59",
                },
                start,
                end,
            )
        selected = scheduling.choose(c, download_ready=True)
        combined = dict(batching.combine(c, selected))
        assert (combined["start_at"], combined["end_at"]) == ("2012-01-01", "2012-02-29")
        assert combined["parameters"]["freq"] == "15min"
        assert c.scalar(text("SELECT count(*) FROM data_sync_jobs WHERE status='SPLIT'")) == 2
        # Rollback-safe ownership: all source windows still exist; no receipts were changed.
        assert planning.split(c, combined)
        c.execute(
            text("UPDATE data_sync_jobs SET status='SPLIT',attempts=1 WHERE request_id=:id"),
            {"id": combined["request_id"]},
        )
        child = scheduling.choose(c, download_ready=True)
        assert child["start_at"] == "2012-01-01"
        assert child["end_at"] < combined["end_at"]
        assert batching.combine(c, child)["request_id"] == child["request_id"]


def test_batching_does_not_absorb_a_downloaded_or_missing_interval(automatic):
    from northstar_quant.data_management.tushare import batching

    with automatic._engine.begin() as c:
        first = add(c, "daily", "2012-01-01")
        downloaded = add(c, "daily", "2012-01-02")
        add(c, "daily", "2012-01-03")
        c.execute(
            text("UPDATE data_sync_jobs SET attempts=1 WHERE request_id=:id"), {"id": downloaded}
        )
        selected = scheduling.choose(c, download_ready=True)
        assert batching.combine(c, selected)["request_id"] == first
        assert c.scalar(text("SELECT count(*) FROM data_sync_jobs WHERE status='SPLIT'")) == 0


def test_observed_native_density_expands_later_requests_without_mutating_receipt(
    automatic, monkeypatch
):
    import json
    from datetime import datetime, timedelta

    from northstar_quant.data_management.tushare import acquisition, batching, jobs

    with automatic._engine.begin() as c:
        planning.enqueue(
            c,
            "1min",
            "RB2610.SHF",
            {
                "ts_code": "RB2610.SHF",
                "freq": "1min",
                "start_date": "2012-01-01 00:00:00",
                "end_date": "2012-01-31 23:59:59",
            },
            "2012-01-01",
            "2012-01-31",
        )
    rows = []
    for day in range(1, 32):
        for minute in range(100):
            stamp = (datetime(2012, 1, day, 9) + timedelta(minutes=minute)).isoformat(sep=" ")
            rows.append(["RB2610.SHF", stamp, 3100, 3100, 3100, 3100, 1, 1])
    payload = json.dumps(
        {
            "code": 0,
            "data": {
                "fields": ["ts_code", "trade_time", "open", "high", "low", "close", "vol", "oi"],
                "items": rows,
            },
        }
    ).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *_: payload)
    published = jobs.process_next(automatic)
    assert published["status"] == "VALIDATED"
    with automatic._engine.begin() as c:
        for start, end in [
            ("2012-02-01", "2012-02-29"),
            ("2012-03-01", "2012-03-31"),
            ("2012-04-01", "2012-04-30"),
        ]:
            planning.enqueue(
                c,
                "1min",
                "RB2610.SHF",
                {
                    "ts_code": "RB2610.SHF",
                    "freq": "1min",
                    "start_date": f"{start} 00:00:00",
                    "end_date": f"{end} 23:59:59",
                },
                start,
                end,
            )
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}'"))
        selected = scheduling.choose(c, download_ready=True)
        combined = batching.combine(c, selected)
        assert (combined["start_at"], combined["end_at"]) == ("2012-02-01", "2012-03-31")
        assert (
            c.scalar(
                text("SELECT row_count FROM data_sync_receipts WHERE receipt_id=:id"),
                {"id": published["receipt_id"]},
            )
            == 3100
        )
