"""Automatic sync durability, secret isolation and immutable provider revisions."""

import json

import httpx2
import pytest
from sqlalchemy import text

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.storage_identity import initialize
from northstar_quant.data_management.tushare import (
    acquisition,
    credentials,
    jobs,
    planning,
    publication,
    settings,
)

TOKEN = "private-test-token-never-returned"


@pytest.fixture
def automatic(postgres_engine, clean_database, tmp_path, monkeypatch):
    from northstar_quant.data_management.tushare.store import initialize as initialize_sync

    with postgres_engine.begin() as connection:
        initialize_sync(connection)
        connection.execute(
            text("""UPDATE data_sync_settings SET enabled=true,
            refresh_at=now()+interval '1 day',next_request_at=now(),revision=1""")
        )
    secret = tmp_path / "secrets"
    monkeypatch.setenv("NORTHSTAR_DATA_SECRET_DIR", str(secret))
    credentials.save(TOKEN)
    market = tmp_path / "market"
    market.mkdir()
    identity = "8600b795-36d0-44b9-80e3-d3b22e805e92"
    initialize(market, identity)
    monkeypatch.setenv("NORTHSTAR_MARKET_DIR", str(market))
    monkeypatch.setenv("NORTHSTAR_MARKET_STORAGE_ID", identity)
    with postgres_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO data_sync_contracts
            (ts_code,exchange,product,kind,details,planned_revision)
            VALUES('RB2610.SHF','SHFE','RB','1','{}',1)""")
        )
        connection.execute(
            text(
                "INSERT INTO data_sync_calendar VALUES('SHFE','2026-09-01',true),"
                "('SHFE','2026-09-02',true)"
            )
        )
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    return library


def pending(library, *, start="2026-09-01", end="2026-09-01"):
    with library._engine.begin() as connection:
        planning.enqueue(
            connection,
            "daily",
            "RB2610.SHF",
            {
                "ts_code": "RB2610.SHF",
                "start_date": start.replace("-", ""),
                "end_date": end.replace("-", ""),
            },
            start,
            end,
        )
        return str(
            connection.scalar(
                text("SELECT request_id FROM data_sync_jobs ORDER BY created_at LIMIT 1")
            )
        )


def response(price=3100.1):
    return json.dumps(
        {
            "code": 0,
            "data": {
                "fields": [
                    "ts_code",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "vol",
                    "amount",
                    "oi",
                ],
                "items": [["RB2610.SHF", "20260901", price, price, price, price, 2, 1.25, 8]],
            },
        }
    ).encode()


def ready(library):
    with library._engine.begin() as connection:
        connection.execute(text("UPDATE data_sync_settings SET next_request_at=now()"))
        connection.execute(
            text("UPDATE data_sync_jobs SET next_at=now() WHERE status IN ('WAITING','PENDING')")
        )


def test_commit_retry_revision_and_backup_pins(automatic, monkeypatch):
    library = automatic
    pending(library)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    result = jobs.process_next(library)
    assert result["status"] == "VALIDATED"
    with library._engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
    snapshot = publication.read_snapshot(row["manifest_hash"], row["manifest_bytes"])
    assert snapshot["rows"][0]["amount_cny"] == "12500.00"
    assert snapshot["rows"][0]["close"] == "3100.1"
    original = row["manifest_hash"]
    # Missing coverage is discovered independently of maximum observed date.
    with library._engine.begin() as connection:
        connection.execute(text("DELETE FROM data_sync_coverage"))
        connection.execute(text("UPDATE data_sync_settings SET refresh_at=now()"))
    planning.refresh(library._engine)
    with library._engine.begin() as connection:
        # Keep bootstrap catalog jobs outside this focused delivery test.
        connection.execute(
            text(
                "UPDATE data_sync_jobs SET next_at=now()+interval '1 day' WHERE dataset='contracts'"
            )
        )
    ready(library)
    with library._engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE data_sync_jobs SET next_at=now()+interval '1 day' WHERE dataset='contracts'"
            )
        )
    assert jobs.process_next(library)["status"] == "VALIDATED"
    with library._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 1
    with library._engine.begin() as connection:
        connection.execute(text("UPDATE data_sync_jobs SET status='PENDING' WHERE dataset='daily'"))
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response(3101.2))
    ready(library)
    with library._engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE data_sync_jobs SET next_at=now()+interval '1 day' WHERE dataset='contracts'"
            )
        )
    jobs.process_next(library)
    assert publication.read_snapshot(original, row["manifest_bytes"]) == snapshot
    from northstar_quant.data_management.library import manifest

    with library._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 2
        references = manifest(connection)
        assert len(references) >= 6
    for item in references:
        library._files.read(item["content_hash"], item["byte_count"])


@pytest.mark.parametrize("stage", ["download", "commit"])
def test_process_death_requeues_only_uncommitted_work(automatic, monkeypatch, stage):
    library = automatic
    pending(library)
    original = jobs._commit
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    if stage == "download":

        def interrupted(*args):
            raise KeyboardInterrupt()

        monkeypatch.setattr(acquisition, "fetch", interrupted)
    else:

        def interrupted_commit(*args):
            original(*args)
            raise KeyboardInterrupt()

        monkeypatch.setattr(jobs, "_commit", interrupted_commit)
    with pytest.raises(KeyboardInterrupt):
        jobs.process_next(library)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    monkeypatch.setattr(jobs, "_commit", original)
    ready(library)
    result = jobs.process_next(library)
    assert result is None if stage == "commit" else result["status"] == "VALIDATED"
    with library._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 1
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 1


def test_row_limit_splits_and_empty_does_not_complete(automatic, monkeypatch):
    library = automatic
    pending(library, end="2026-09-02")
    payload = json.loads(response())
    payload["data"]["items"] *= 2000
    monkeypatch.setattr(acquisition, "fetch", lambda *a: json.dumps(payload).encode())
    assert jobs.process_next(library)["status"] == "SPLIT"
    payload["data"]["items"] = []
    monkeypatch.setattr(acquisition, "fetch", lambda *a: json.dumps(payload).encode())
    ready(library)
    assert jobs.process_next(library)["status"] == "WAITING"
    with library._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0


def test_failed_storage_does_not_advance_coverage(automatic, monkeypatch):
    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    monkeypatch.setattr(
        publication, "publish", lambda *a: (_ for _ in ()).throw(OSError("disk full"))
    )
    assert jobs.process_next(automatic)["status"] == "BLOCKED"
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0


def test_ui_token_is_write_only_and_manual_interfaces_are_absent(automatic):
    from northstar_quant.apps.data_hub import create_app
    from tests.apps.browser import ProtocolClient as TestClient

    app = create_app(automatic._engine, automatic)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/sync/token", json={"token": TOKEN}).status_code == 403
        csrf = client.get("/api/browser-session").json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        saved = client.post("/api/sync/token", json={"token": TOKEN})
        assert saved.status_code == 200, saved.text
        assert TOKEN not in saved.text
        assert saved.json()["token_configured"]
        for path in (
            "/api/import",
            "/api/sync/tushare",
            "/api/sources/8600b795-36d0-44b9-80e3-d3b22e805e92/reprocess",
        ):
            assert client.post(path, json={}).status_code in (404, 405)
        config = saved.json()["settings"]
        assert client.post(
            "/api/sync/settings",
            json={"revision": config["revision"], "enabled": False, "products": ["RB"]},
        ).status_code in (400, 422)
        assert (
            client.post(
                "/api/sync/settings", json={"revision": config["revision"], "enabled": False}
            ).status_code
            == 200
        )
    assert (credentials.root() / "tushare.token").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("mode", ["reflect", "permission", "timeout"])
def test_no_secret_in_network_errors(mode):
    def handle(request):
        if mode == "timeout":
            raise httpx2.ReadTimeout(TOKEN)
        if mode == "reflect":
            return httpx2.Response(200, content=TOKEN.encode())
        return httpx2.Response(200, json={"code": 2002, "msg": TOKEN})

    with pytest.raises(acquisition.DownloadError) as caught:
        acquisition.fetch("fut_daily", {}, TOKEN, transport=httpx2.MockTransport(handle))
    assert TOKEN not in str(caught.value)


def test_planning_all_capabilities_is_idempotent_and_stops_at_expiry(automatic, monkeypatch):
    from datetime import date

    from northstar_quant.data_management.tushare.catalog import BY_KEY

    monkeypatch.setattr(planning, "target_day", lambda: date(2026, 9, 9))
    with automatic._engine.begin() as connection:
        connection.execute(text("DELETE FROM data_sync_contracts"))
        for kind, code in [("1", "RB2609.SHF"), ("2", "RB.SHF")]:
            connection.execute(
                text("""INSERT INTO data_sync_contracts(ts_code,exchange,product,kind,details)
                VALUES(:code,'SHFE','RB',:kind,CAST(:details AS jsonb))"""),
                {
                    "code": code,
                    "kind": kind,
                    "details": json.dumps({"list_date": "20260901", "delist_date": "20260903"}),
                },
            )
    planning.plan(automatic._engine)
    with automatic._engine.connect() as connection:
        rows = connection.execute(text("SELECT * FROM data_sync_jobs")).mappings().all()
        count = len(rows)
        assert {row["dataset"] for row in rows} == set(BY_KEY) - {"contracts"}
        assert all(
            row["end_at"] <= "2026-09-03"
            for row in rows
            if row["dataset"] not in ("calendar", "holdings", "warehouse", "index", "weekly_detail")
        )
    planning.plan(automatic._engine)
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_jobs")) == count


def test_parallel_worker_cannot_take_active_request_and_pause_is_responsive(automatic, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    pending(automatic)
    entered, release = Event(), Event()

    def fetch(*args):
        entered.set()
        assert release.wait(10)
        return response()

    monkeypatch.setattr(acquisition, "fetch", fetch)
    with ThreadPoolExecutor() as pool:
        first = pool.submit(jobs.process_next, automatic)
        assert entered.wait(10)
        try:
            assert jobs.process_next(automatic) is None
            config = settings.status(automatic._engine)["settings"]
            settings.configure(automatic._engine, revision=config["revision"], enabled=False)
        finally:
            release.set()
        assert first.result()["status"] == "VALIDATED"
    assert jobs.process_next(automatic) is None


def test_files_saved_before_commit_can_be_reused_after_crash(automatic, monkeypatch):
    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *args: response())
    original = jobs._commit
    monkeypatch.setattr(jobs, "_commit", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        jobs.process_next(automatic)
    inventory = automatic._files.inventory()
    assert len(inventory) == 3
    ready(automatic)
    monkeypatch.setattr(jobs, "_commit", original)
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    assert automatic._files.inventory() == inventory


def test_stale_generation_cannot_publish_after_recovery(automatic, monkeypatch):
    from uuid import uuid4

    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *args: response())
    original = jobs._commit

    def lost_owner(engine, selected, *args):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE data_sync_jobs SET generation=:g,status='WAITING' WHERE request_id=:id"
                ),
                {"g": uuid4(), "id": selected["request_id"]},
            )
        original(engine, selected, *args)

    monkeypatch.setattr(jobs, "_commit", lost_owner)
    assert jobs.process_next(automatic)["status"] == "WAITING"
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 0


def test_daily_missing_middle_day_cannot_advance_coverage(automatic, monkeypatch):
    pending(automatic, end="2026-09-02")
    monkeypatch.setattr(acquisition, "fetch", lambda *args: response())
    assert jobs.process_next(automatic)["status"] == "WAITING"
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0


def test_joint_restore_preserves_downloads_and_fixed_publication(automatic, monkeypatch, tmp_path):
    from northstar_quant.apps.maintenance import backup, restore
    from tests.apps.test_maintenance import _empty_restore_database

    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *args: response())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    destination = tmp_path / "backup"
    backup(automatic._engine, automatic._files, destination)
    with _empty_restore_database(automatic._engine) as restored:
        restore(restored, tmp_path / "restored-sources", destination)
        with restored.connect() as connection:
            row = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
        assert (
            publication.read_snapshot(row["manifest_hash"], row["manifest_bytes"])["row_count"] == 1
        )
