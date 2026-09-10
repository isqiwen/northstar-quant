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
    request_id = pending(library)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    result = jobs.process_next(library)
    assert result["status"] == "VALIDATED"
    with library._engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
    snapshot = publication.read_snapshot(row["manifest_hash"], row["manifest_bytes"])
    assert snapshot["rows"][0]["amount_cny"] == "12500"
    assert snapshot["rows"][0]["close"] == "3100.1"
    original = row["manifest_hash"]
    # Missing coverage is discovered independently of maximum observed date.
    with library._engine.begin() as connection:
        connection.execute(text("DELETE FROM data_sync_coverage"))
        connection.execute(text("UPDATE data_sync_settings SET refresh_at=now()-interval '1 day'"))
    planning.refresh(library._engine)
    # This case verifies coverage repair of an existing window, not new catalog planning.
    monkeypatch.setattr(planning, "plan", lambda *_: None)
    with library._engine.begin() as connection:
        # Keep other windows outside this focused delivery test.
        connection.execute(
            text("UPDATE data_sync_jobs SET next_at=now()+interval '1 day' WHERE request_id<>:id"),
            {"id": request_id},
        )
    ready(library)
    with library._engine.begin() as connection:
        connection.execute(
            text("UPDATE data_sync_jobs SET next_at=now()+interval '1 day' WHERE request_id<>:id"),
            {"id": request_id},
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
            text("UPDATE data_sync_jobs SET next_at=now()+interval '1 day' WHERE request_id<>:id"),
            {"id": request_id},
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
        publication, "publish", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full"))
    )
    assert jobs.process_next(automatic)["status"] == "BLOCKED"
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0


def test_ui_token_is_write_only_and_manual_interfaces_are_absent(automatic, monkeypatch):
    from northstar_quant.apps.data_hub import create_app
    from tests.apps.browser import ProtocolClient as TestClient

    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    completed = jobs.process_next(automatic)
    replay = {
        "request_id": completed["request_id"],
        "source_generation": completed["reprocess_source"]["generation"],
    }
    app = create_app(automatic._engine, automatic)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/sync/reprocess", json=replay).status_code == 403
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
        queued = client.post("/api/sync/reprocess", json=replay)
        assert queued.status_code == 200, queued.text
        assert queued.json()["source_generation"] == replay["source_generation"]
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


@pytest.mark.parametrize(
    "field,value", [("open", None), ("close", "--"), ("vol", "invalid"), ("amount", "1e26")]
)
def test_bad_numeric_is_retained_and_does_not_kill_sync(automatic, monkeypatch, field, value):
    pending(automatic)
    raw = json.loads(response())
    raw["data"]["items"][0][raw["data"]["fields"].index(field)] = value
    monkeypatch.setattr(acquisition, "fetch", lambda *_: json.dumps(raw).encode())
    blocked = jobs.process_next(automatic)
    assert blocked["status"] == "BLOCKED"
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT source_hash FROM data_sync_attempts"))
        assert connection.scalar(text("SELECT enabled FROM data_sync_settings"))
    pending(automatic, start="2026-09-02", end="2026-09-02")
    monkeypatch.setattr(
        acquisition, "fetch", lambda *_: response().replace(b"20260901", b"20260902")
    )
    ready(automatic)
    assert jobs.process_next(automatic)["status"] == "VALIDATED"


def test_missing_price_is_retained_then_corrected_response_can_publish(automatic, monkeypatch):
    pending(automatic)
    raw = json.loads(response())
    index = raw["data"]["fields"].index("high")
    raw["data"]["fields"].pop(index)
    raw["data"]["items"][0].pop(index)
    incomplete = json.dumps(raw).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *_: incomplete)
    failed = jobs.process_next(automatic)
    assert failed["status"] == "BLOCKED"
    assert "high" in failed["error"]
    report = failed["attempts_detail"][0]["quality"]
    assert report["issues"][0]["fields"] == ["high"]
    assert report["issues"][0]["row_number"] is None
    with automatic._engine.connect() as connection:
        attempt = connection.execute(text("SELECT * FROM data_sync_attempts")).mappings().one()
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 0
    assert automatic._files.read(attempt["source_hash"], attempt["source_bytes"]) == incomplete
    assert publication.storage().inventory() == []
    # The existing UI retry operation requeues the failed job; no alternate import path.
    monkeypatch.setattr(planning, "plan", lambda *_: None)
    monkeypatch.setattr(planning, "refresh", lambda *_: None)
    config = settings.status(automatic._engine)["settings"]
    settings.configure(automatic._engine, revision=config["revision"], enabled=True)
    monkeypatch.setattr(acquisition, "fetch", lambda *_: response())
    ready(automatic)
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        receipt = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
        assert connection.scalar(text("SELECT count(*) FROM data_sync_attempts")) == 2
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 1
    assert (
        publication.read_snapshot(receipt["manifest_hash"], receipt["manifest_bytes"])["rows"][0][
            "high"
        ]
        == "3100.1"
    )
    assert automatic._files.read(attempt["source_hash"], attempt["source_bytes"]) == incomplete
    with automatic._engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT quality FROM data_sync_attempts WHERE generation=:g"),
                {"g": attempt["generation"]},
            )
            == report
        )


def test_new_quality_rule_keeps_old_receipt_and_reuses_unchanged_parquet(automatic, monkeypatch):
    from northstar_quant.data_management.tushare import quality

    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *_: response())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        before = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
    original = publication.read_snapshot(before["manifest_hash"], before["manifest_bytes"])
    monkeypatch.setattr(quality, "RULE", "test-quality-revision")
    for _ in range(2):
        with automatic._engine.begin() as connection:
            connection.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
        ready(automatic)
        assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        receipts = (
            connection.execute(text("SELECT * FROM data_sync_receipts ORDER BY created_at"))
            .mappings()
            .all()
        )
        current = connection.scalar(text("SELECT receipt_id FROM data_sync_coverage"))
    assert len(receipts) == 2
    assert current == receipts[1]["receipt_id"]
    assert receipts[0]["source_hash"] == receipts[1]["source_hash"]
    assert receipts[0]["parquet_hash"] == receipts[1]["parquet_hash"]
    assert receipts[0]["content_hash"] != receipts[1]["content_hash"]
    assert receipts[0]["quality"]["rule"] != receipts[1]["quality"]["rule"]
    assert publication.read_snapshot(before["manifest_hash"], before["manifest_bytes"]) == original


def test_permission_failure_cannot_claim_any_data_coverage(automatic, monkeypatch):
    pending(automatic)

    def denied(*_):
        acquisition.decode(
            json.dumps({"code": 2002, "msg": "没有访问该接口的权限 " + TOKEN}).encode()
        )

    monkeypatch.setattr(acquisition, "fetch", denied)
    failed = jobs.process_next(automatic)
    assert failed["status"] == "BLOCKED"
    assert "权限" in failed["error"]
    assert TOKEN not in json.dumps(failed)
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 0
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0
        assert connection.scalar(text("SELECT enabled FROM data_sync_settings"))


def test_invalid_refresh_keeps_last_published_files_and_coverage(automatic, monkeypatch):
    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *_: response())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        original = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
        coverage = connection.execute(text("SELECT * FROM data_sync_coverage")).mappings().one()
    snapshot = publication.read_snapshot(original["manifest_hash"], original["manifest_bytes"])
    raw = json.loads(response())
    field = raw["data"]["fields"].index("close")
    raw["data"]["fields"].pop(field)
    raw["data"]["items"][0].pop(field)
    incomplete = json.dumps(raw).encode()
    with automatic._engine.begin() as connection:
        connection.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
    monkeypatch.setattr(acquisition, "fetch", lambda *_: incomplete)
    ready(automatic)
    failed = jobs.process_next(automatic)
    assert failed["status"] == "BLOCKED"
    assert failed["receipt_id"] == str(original["receipt_id"])
    with automatic._engine.connect() as connection:
        assert (
            connection.execute(text("SELECT * FROM data_sync_coverage")).mappings().one()
            == coverage
        )
        assert (
            connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
            == original
        )
        attempt = (
            connection.execute(text("SELECT * FROM data_sync_attempts WHERE outcome='BLOCKED'"))
            .mappings()
            .one()
        )
    assert automatic._files.read(attempt["source_hash"], attempt["source_bytes"]) == incomplete
    assert (
        publication.read_snapshot(original["manifest_hash"], original["manifest_bytes"]) == snapshot
    )


def test_normalized_decimal_publication_keeps_source_and_semantic_retry_identity(
    automatic, monkeypatch
):
    import io
    from decimal import Decimal, localcontext

    import pyarrow as pa
    import pyarrow.parquet as pq

    from northstar_quant.data_management.exploration.rows import read

    request_id = pending(automatic)
    original = response("3100.1000")
    monkeypatch.setattr(acquisition, "fetch", lambda *_: original)
    with localcontext() as context:
        context.prec = 5
        assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        receipt = dict(
            connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
        )
    snapshot = publication.read_snapshot(receipt["manifest_hash"], receipt["manifest_bytes"])
    assert snapshot["source"] == {
        "content_hash": receipt["source_hash"],
        "byte_count": receipt["source_bytes"],
    }
    assert automatic._files.read(receipt["source_hash"], receipt["source_bytes"]) == original
    table = pq.read_table(
        io.BytesIO(automatic._files.read(receipt["parquet_hash"], receipt["parquet_bytes"]))
    )
    assert table.schema.field("close").type == pa.decimal128(38, 12)
    assert table["close"].to_pylist() == [Decimal("3100.1")]
    assert table["amount_cny"].to_pylist() == [Decimal("12500")]
    view = read(
        automatic._engine,
        "daily",
        "RB2610.SHF",
        "2026-09-01",
        "2026-09-01",
        [receipt["receipt_id"]],
    )
    assert view["rows"][0]["close"] == "3100.1"
    # A supplier spelling-only revision retains both raw attempts but no second economic version.
    changed = response("3.1001e3")
    monkeypatch.setattr(acquisition, "fetch", lambda *_: changed)
    with automatic._engine.begin() as connection:
        connection.execute(
            text("UPDATE data_sync_jobs SET status='PENDING' WHERE request_id=:id"),
            {"id": request_id},
        )
    ready(automatic)
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 1
        attempts = (
            connection.execute(
                text("SELECT source_hash, source_bytes FROM data_sync_attempts ORDER BY started_at")
            )
            .mappings()
            .all()
        )
    assert len(attempts) == 2
    assert {automatic._files.read(a["source_hash"], a["source_bytes"]) for a in attempts} == {
        original,
        changed,
    }
    assert (
        publication.read_snapshot(receipt["manifest_hash"], receipt["manifest_bytes"]) == snapshot
    )


def test_retained_reprocessing_survives_pause_and_preserves_versions(automatic, monkeypatch):
    from uuid import UUID

    from northstar_quant.data_management.tushare import quality, reprocessing

    library = automatic
    request_id = UUID(pending(library))
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    original = jobs.process_next(library)
    source = UUID(original["reprocess_source"]["generation"])
    old_receipt = original["receipt_id"]
    with library._engine.begin() as connection:
        old = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
        old_snapshot = publication.read_snapshot(old["manifest_hash"], old["manifest_bytes"])
        connection.execute(text("UPDATE data_sync_settings SET enabled=false"))
    queued = reprocessing.enqueue(library._engine, request_id=request_id, source_generation=source)
    assert queued["status"] == "PENDING"
    assert jobs.process_next(library) is None
    assert reprocessing.enqueue(library._engine, request_id=request_id, source_generation=source)[
        "source_generation"
    ] == str(source)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: pytest.fail("must not download"))
    monkeypatch.setattr(credentials, "read", lambda: pytest.fail("must not require credentials"))
    monkeypatch.setattr(quality, "RULE", "test-reprocessing-rule")
    with library._engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE data_sync_settings SET enabled=true, next_request_at=now()+interval '1 day'"
            )
        )
    # A new library/worker observes the durable request without the original Web caller.
    reopened = DataLibrary(library._engine, SourceFiles(library._files.root))
    result = jobs.process_next(reopened)
    assert result["status"] == "VALIDATED"
    assert result["receipt_id"] != old_receipt
    assert result["attempts"] == original["attempts"]
    assert result["checked_at"] == original["checked_at"]
    attempt = result["attempts_detail"][0]
    assert attempt["parent_generation"] == str(source)
    assert attempt["receipt_id"] == result["receipt_id"]
    assert attempt["code_revision"]
    assert publication.read_snapshot(old["manifest_hash"], old["manifest_bytes"]) == old_snapshot
    reprocessing.enqueue(library._engine, request_id=request_id, source_generation=source)
    again = jobs.process_next(reopened)
    assert again["receipt_id"] == result["receipt_id"]
    with library._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 2


def test_reprocessing_rejects_stale_source_and_corruption(automatic, monkeypatch):
    from uuid import UUID, uuid4

    from northstar_quant.data_management.tushare import reprocessing

    library = automatic
    request_id = UUID(pending(library))
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    old = jobs.process_next(library)
    with library._engine.begin() as connection:
        connection.execute(
            text("UPDATE data_sync_jobs SET status='PENDING',next_at=now() WHERE request_id=:id"),
            {"id": request_id},
        )
    with pytest.raises(ValueError, match="下载已排队"):
        reprocessing.enqueue(
            library._engine,
            request_id=request_id,
            source_generation=UUID(old["reprocess_source"]["generation"]),
        )
    ready(library)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response(3200))
    current = jobs.process_next(library)
    with pytest.raises(ValueError, match="最新"):
        reprocessing.enqueue(
            library._engine,
            request_id=request_id,
            source_generation=UUID(old["reprocess_source"]["generation"]),
        )
    with pytest.raises(ValueError, match="最新"):
        reprocessing.enqueue(library._engine, request_id=request_id, source_generation=uuid4())
    source = current["reprocess_source"]
    reprocessing.enqueue(
        library._engine, request_id=request_id, source_generation=UUID(source["generation"])
    )
    monkeypatch.setattr(acquisition, "fetch", lambda *a: pytest.fail("no fallback download"))
    library._files._path(source["source_hash"]).write_bytes(b"corrupt")
    result = jobs.process_next(library)
    assert result["status"] == "BLOCKED"
    assert "缺失或损坏" in result["error"]
    assert result["receipt_id"] == current["receipt_id"]
    with library._engine.connect() as connection:
        assert (
            str(
                connection.scalar(
                    text("SELECT receipt_id FROM data_sync_coverage WHERE request_id=:id"),
                    {"id": request_id},
                )
            )
            == current["receipt_id"]
        )


def test_interrupted_reprocessing_keeps_the_same_source(automatic, monkeypatch):
    from uuid import UUID

    from northstar_quant.data_management.tushare import reprocessing

    library = automatic
    request_id = UUID(pending(library))
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    original = jobs.process_next(library)
    source = UUID(original["reprocess_source"]["generation"])
    reprocessing.enqueue(library._engine, request_id=request_id, source_generation=source)
    publish = publication.publish
    monkeypatch.setattr(acquisition, "fetch", lambda *a: pytest.fail("no download"))
    monkeypatch.setattr(
        publication, "publish", lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        jobs.process_next(library)
    with pytest.raises(ValueError, match="正在处理"):
        reprocessing.enqueue(library._engine, request_id=request_id, source_generation=source)
    monkeypatch.setattr(publication, "publish", publish)
    result = jobs.process_next(library)
    assert result["status"] == "VALIDATED"
    assert result["receipt_id"] == original["receipt_id"]
    assert result["attempts_detail"][1]["outcome"] == "INTERRUPTED"
    assert result["attempts_detail"][0]["parent_generation"] == str(source)


@pytest.mark.parametrize("permission", [False, True])
def test_only_confirmed_permission_failure_blocks_other_windows(automatic, monkeypatch, permission):
    pending(automatic)
    message = "没有访问该接口的权限" if permission else "请求范围无效"

    def rejected(*_):
        acquisition.decode(json.dumps({"code": 40203, "msg": message + TOKEN}).encode())

    monkeypatch.setattr(acquisition, "fetch", rejected)
    failed = jobs.process_next(automatic)
    assert failed["status"] == "BLOCKED"
    assert TOKEN not in failed["error"]
    pending(automatic, start="2026-09-02", end="2026-09-02")
    ready(automatic)
    monkeypatch.setattr(
        acquisition, "fetch", lambda *_: response().replace(b"20260901", b"20260902")
    )
    following = jobs.process_next(automatic)
    if permission:
        assert following is None
    else:
        assert following["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == int(
            not permission
        )
        assert (
            connection.scalar(
                text("SELECT status FROM data_sync_jobs WHERE request_id=:id"),
                {"id": failed["request_id"]},
            )
            == "BLOCKED"
        )
