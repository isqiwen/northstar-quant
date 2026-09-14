"""Durable initial boundaries, fair history selection and truthful lane progress."""

from datetime import date
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


def test_initial_history_starts_oldest_month_and_rotates_data_types(automatic):
    with automatic._engine.begin() as c:
        first = add(c, "daily", "2012-01-01", "A")
        add(c, "daily", "2012-01-01", "B")
        other = add(c, "1min", "2012-01-02")
        add(c, "5min", "2012-02-01")
        add(c, "daily", "2026-09-13")
        c.execute(text("UPDATE data_sync_jobs SET created_at='2026-09-14T02:00:00Z'"))
        # Both initial types eligible; once daily has a turn, 1min wins over daily B.
        attempt(c, first)
        assert scheduling.choose(c, download_ready=True)["request_id"] == other
        c.execute(
            text("UPDATE data_sync_jobs SET status='BLOCKED' WHERE request_id=:r"), {"r": other}
        )
        assert scheduling.choose(c, download_ready=True)["scope"] == "B"


def test_new_days_preempt_backfill_and_boundary_survives_restart_and_settings_edits(automatic):
    with automatic._engine.begin() as c:
        old = add(c, "daily", "2012-01-01")
        c.execute(text("UPDATE data_sync_jobs SET created_at='2026-09-14T02:00:00Z'"))
        new = add(c, "daily", "2026-09-14")
        c.execute(text("UPDATE data_sync_settings SET revision=revision+1,updated_at=now()"))
    with automatic._engine.begin() as c:
        assert scheduling.history_end(c) == date(2026, 9, 13)
        assert scheduling.choose(c, download_ready=True)["request_id"] == new
        attempt(c, new)
        assert scheduling.choose(c, download_ready=True)["request_id"] == old
        assert scheduling.choose(c, download_ready=False) is None


def test_lane_progress_excludes_split_and_does_not_count_empty_or_blocked_as_complete(automatic):
    with automatic._engine.begin() as c:
        old = add(c, "daily", "2012-01-01")
        split = add(c, "daily", "2012-01-02")
        blocked = add(c, "1min", "2012-01-01")
        c.execute(text("UPDATE data_sync_jobs SET created_at='2026-09-14T02:00:00Z'"))
        new = add(c, "daily", "2026-09-14")
        attempt(c, old)
        c.execute(
            text("UPDATE data_sync_jobs SET status='SPLIT' WHERE request_id=:r"), {"r": split}
        )
        c.execute(
            text("UPDATE data_sync_jobs SET status='BLOCKED' WHERE request_id=:r"), {"r": blocked}
        )
        c.execute(
            text("UPDATE data_sync_jobs SET status='VALIDATED' WHERE request_id=:r"), {"r": new}
        )
    result = settings.status(automatic._engine)
    history, daily = result["lanes"]
    assert result["settings"]["history_end"] == "2026-09-13"
    assert (history["total"], history["validated"], history["waiting"], history["blocked"]) == (
        2,
        0,
        1,
        1,
    )
    assert history["oldest_pending"] == "2012-01-01"
    assert (daily["total"], daily["validated"]) == (1, 1)


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
