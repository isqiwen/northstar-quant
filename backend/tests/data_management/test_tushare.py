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
from tests.apps.browser import login_response

TOKEN = "private-test-token-never-returned"


@pytest.fixture
def automatic(postgres_engine, clean_database, tmp_path, monkeypatch):
    from northstar_quant.data_management.tushare.store import initialize as initialize_sync

    with postgres_engine.begin() as connection:
        initialize_sync(connection)
        connection.execute(
            text("""UPDATE data_sync_settings SET enabled=true,
            refresh_at=now()+interval '1 day',api_next_at='{}',next_request_at=now(),revision=1""")
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
            VALUES('RB2610.SHF','SHFE','RB','1','{"list_date":"19900101","delist_date":"20260902","last_ddate":"20260903"}',1)""")
        )
        connection.execute(
            text("""INSERT INTO data_contract_collections(scope,start_date,end_date)
            VALUES('RB2610.SHF','1990-01-01','2026-09-02')""")
        )
        connection.execute(
            text(
                "INSERT INTO data_sync_calendar VALUES('SHFE','2026-09-01',true),"
                "('SHFE','2026-09-02',true)"
            )
        )
    monkeypatch.setenv("NORTHSTAR_DATA_DIR", str(tmp_path / "sources"))
    monkeypatch.delenv("NORTHSTAR_STORAGE_ID", raising=False)
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
        connection.execute(
            text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()")
        )
        connection.execute(
            text("UPDATE data_sync_jobs SET next_at=now() WHERE status IN ('WAITING','PENDING')")
        )


@pytest.mark.parametrize("omit_close_today", [False, True])
def test_settlement_fields_survive_download_and_fixed_publication(
    automatic, monkeypatch, omit_close_today
):
    from northstar_quant.data_management.tushare.catalog import BY_KEY

    parameters = {"ts_code": "RB2610.SHF", "start_date": "20260901", "end_date": "20260901"}
    with automatic._engine.begin() as connection:
        planning.enqueue(
            connection, "settlement", "RB2610.SHF", parameters, "2026-09-01", "2026-09-01"
        )
        job = connection.execute(text("SELECT * FROM data_sync_jobs")).mappings().one()
    assert "fields" not in parameters
    selected = BY_KEY["settlement"].fields
    assert job["parameters"]["fields"] == ",".join(selected)
    values = {
        "ts_code": "RB2610.SHF",
        "trade_date": "20260901",
        "exchange": "SHFE",
        "settle": "3100.125",
        "trading_fee_rate": "0.050",
        "offset_today_fee": None,
    }
    returned = [
        field for field in selected if not (omit_close_today and field == "offset_today_fee")
    ]
    raw = json.dumps(
        {
            "code": 0,
            "data": {"fields": returned, "items": [[values.get(field) for field in returned]]},
        }
    ).encode()

    def handle(request):
        payload = json.loads(request.content)
        assert payload["api_name"] == "fut_settle"
        assert payload["fields"] == ",".join(selected)
        assert payload["params"] == parameters
        return httpx2.Response(200, content=raw)

    fetch = acquisition.fetch
    monkeypatch.setattr(
        acquisition,
        "fetch",
        lambda *args: fetch(*args, transport=httpx2.MockTransport(handle)),
    )
    result = jobs.process_next(automatic)
    with automatic._engine.connect() as connection:
        attempt = connection.execute(text("SELECT * FROM data_sync_attempts")).mappings().one()
        receipts = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().all()
    assert automatic._files.read(attempt["source_hash"], attempt["source_bytes"]) == raw
    assert job["parameters"]["fields"] == ",".join(selected)
    assert result["status"] == "VALIDATED"
    assert result["origin"]["first_observed"] == "2026-09-01"
    assert result["origin"]["source_hash"] == attempt["source_hash"]
    receipt = receipts[0]
    snapshot = publication.read_snapshot(receipt["manifest_hash"], receipt["manifest_bytes"])
    assert snapshot["parameters"]["fields"] == ",".join(selected)
    assert snapshot["rows"][0]["trading_fee_rate"] == "0.05"
    assert snapshot["rows"][0].get("offset_today_fee") is None
    import io
    from decimal import Decimal

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(
        io.BytesIO(automatic._files.read(receipt["parquet_hash"], receipt["parquet_bytes"]))
    )
    assert table.schema.field("trading_fee_rate").type == pa.decimal128(38, 12)
    assert table["trading_fee_rate"].to_pylist() == [Decimal("0.05")]
    if omit_close_today:
        assert "offset_today_fee" not in table.column_names
    else:
        assert table["offset_today_fee"].to_pylist() == [None]
    assert snapshot["quality"]["normalization"]["settlement_rate_basis"] == (
        "SUPPLIER_REPORTED_NO_UNIT_CONVERSION"
    )
    # Numeric spelling changes preserve economic identity, but keep both raw responses.
    first_raw = raw
    raw = raw.replace(b'"0.050"', b'"5e-2"')
    with automatic._engine.begin() as connection:
        connection.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
    ready(automatic)
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 1
    assert automatic._files.read(receipt["source_hash"], receipt["source_bytes"]) == first_raw
    from northstar_quant.data_management.exploration.revisions import compare
    from northstar_quant.data_management.exploration.rows import read

    view = read(
        automatic._engine,
        "settlement",
        "RB2610.SHF",
        "2026-09-01",
        "2026-09-01",
        [receipt["receipt_id"]],
    )
    assert view["rows"][0]["settle"] == "3100.125"
    assert view["rows"][0]["trading_fee_rate"] == "0.05"
    assert view["rows"][0].get("offset_today_fee") is None
    raw = raw.replace(b'"5e-2"', b'"0.060"')
    with automatic._engine.begin() as connection:
        connection.execute(text("UPDATE data_sync_jobs SET status='PENDING'"))
    ready(automatic)
    revised = jobs.process_next(automatic)
    assert revised["status"] == "VALIDATED"
    with automatic._engine.connect() as connection:
        after_id = connection.scalar(
            text("SELECT receipt_id FROM data_sync_receipts WHERE receipt_id<>:id"),
            {"id": receipt["receipt_id"]},
        )
    difference = compare(automatic._engine, before_id=receipt["receipt_id"], after_id=after_id)
    assert difference["counts"]["changed"] == 1
    assert [(item["field"], item["before"], item["after"]) for item in difference["changes"]] == [
        ("trading_fee_rate", "0.05", "0.06")
    ]
    assert (
        read(
            automatic._engine,
            "settlement",
            "RB2610.SHF",
            "2026-09-01",
            "2026-09-01",
            [receipt["receipt_id"]],
        )["rows"]
        == view["rows"]
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
        assert client.post("/api/sync/reprocess", json=replay).status_code == 401
        assert client.post("/api/sync/token", json={"token": TOKEN}).status_code == 401
        csrf = login_response(client).json()["csrf"]
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


def test_planning_applicable_data_is_idempotent_and_stops_at_expiry(automatic, monkeypatch):
    from datetime import date

    from northstar_quant.data_management.tushare.catalog import BY_KEY

    monkeypatch.setattr(planning, "target_day", lambda: date(2026, 9, 9))
    with automatic._engine.begin() as connection:
        connection.execute(text("DELETE FROM data_contract_collections"))
        connection.execute(text("DELETE FROM data_sync_contracts"))
        for kind, code in [("1", "RB2609.SHF"), ("2", "RB.SHF")]:
            connection.execute(
                text("""INSERT INTO data_sync_contracts(ts_code,exchange,product,kind,details)
                VALUES(:code,'SHFE','RB',:kind,CAST(:details AS jsonb))"""),
                {
                    "code": code,
                    "kind": kind,
                    "details": json.dumps(
                        {
                            "list_date": "20260901",
                            "delist_date": "20260903",
                            "last_ddate": "20260904",
                            "d_mode_desc": "实物交割",
                        }
                    ),
                },
            )
    planning.plan(automatic._engine)
    with automatic._engine.connect() as connection:
        rows = connection.execute(text("SELECT * FROM data_sync_jobs")).mappings().all()
        count = len(rows)
        assert {row["dataset"] for row in rows} == set(BY_KEY) - {
            "contracts",
            "continuous",
            "mapping",
            "adjusted",
            "index",
        }
        assert all(
            row["end_at"] <= "2026-09-03"
            for row in rows
            if row["dataset"]
            not in ("calendar", "holdings", "warehouse", "index", "weekly_detail", "week", "month")
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
    monkeypatch.setattr(
        acquisition, "fetch", lambda *args: pytest.fail("durable raw must not redownload")
    )
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
    from northstar_quant.data_management.backup import backup, restore
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
    assert len(publication.storage().inventory()) == 1  # Private rejected raw response only.
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
                "UPDATE data_sync_settings SET enabled=true, "
                "api_next_at=jsonb_build_object('fut_daily',now()+interval '1 day'), "
                "next_request_at=now()+interval '1 day'"
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


@pytest.mark.parametrize(
    "message,label,retry",
    [
        ("积分不足", "权限不足", False),
        ("参数不正确", "参数或范围", False),
        ("接口不存在", "接口不可用", False),
        ("每天最多调用", "限频", True),
        ("unclassified", "原因未确认", False),
    ],
)
def test_provider_failure_classification_never_echoes_secrets(message, label, retry):
    with pytest.raises(acquisition.DownloadError) as caught:
        acquisition.decode(json.dumps({"code": 50101, "msg": message + TOKEN}).encode())
    assert label in str(caught.value)
    assert TOKEN not in str(caught.value)
    assert caught.value.retry is retry


def test_historical_empty_remains_uncovered_with_slow_automatic_recheck(automatic, monkeypatch):
    from datetime import UTC, date, datetime, timedelta

    monkeypatch.setattr(planning, "target_day", lambda: date(2026, 9, 9))
    with automatic._engine.begin() as c:
        planning.enqueue(
            c,
            "15min",
            "RB2610.SHF",
            {"ts_code": "RB2610.SHF", "freq": "15min"},
            "2012-04-17",
            "2012-04-30",
        )
        c.execute(text("UPDATE data_sync_jobs SET attempts=6"))
    payload = json.loads(response())
    payload["data"]["items"] = []
    monkeypatch.setattr(acquisition, "fetch", lambda *_: json.dumps(payload).encode())
    before = datetime.now(UTC)
    result = jobs.process_next(automatic)
    assert result["status"] == "WAITING"
    assert "起点探测" in result["error"]
    assert datetime.fromisoformat(result["next_at"]) >= before + timedelta(days=90)
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0
    assert jobs.process_next(automatic) is None


def test_catalog_arrival_keeps_real_and_series_request_ownership_separate(automatic, monkeypatch):
    from datetime import date

    monkeypatch.setattr(planning, "target_day", lambda: date(2026, 9, 9))
    with automatic._engine.begin() as c:
        c.execute(text("DELETE FROM data_contract_collections"))
        c.execute(text("DELETE FROM data_sync_contracts"))
        c.execute(
            text("INSERT INTO data_sync_contracts VALUES ('A.DCE','DCE','A','2','{}',NULL,0)")
        )
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        assert c.scalar(text("SELECT count(*) FROM data_sync_jobs")) == 0
        planning.enqueue(c, "contracts", "DCE", {"exchange": "DCE", "fut_type": "1"}, "", "")
    row = {
        "ts_code": "A2609.DCE",
        "exchange": "DCE",
        "fut_code": "A",
        "list_date": "20260901",
        "delist_date": "20260908",
        "last_ddate": "20260908",
    }
    payload = json.dumps(
        {"code": 0, "data": {"fields": list(row), "items": [list(row.values())]}}
    ).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *_: payload)
    completed = jobs.process_next(automatic)
    assert completed["status"] == "VALIDATED", completed
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        assert (
            c.scalar(text("SELECT planning_error FROM data_sync_contracts WHERE ts_code='A.DCE'"))
            is None
        )
        assert (
            c.scalar(text("SELECT count(*) FROM data_contract_requests WHERE scope='A.DCE'")) == 0
        )
        assert c.scalar(text("SELECT count(*) FROM data_series_requests WHERE scope='A.DCE'")) > 0
        count = c.scalar(text("SELECT count(*) FROM data_sync_jobs WHERE scope='A2609.DCE'"))
        assert count > 0
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        assert (
            c.scalar(text("SELECT count(*) FROM data_sync_jobs WHERE scope='A2609.DCE'")) == count
        )
    visible = settings.status(automatic._engine)["jobs"]
    # Completed/attempted work cannot be displaced by the flood of newly planned jobs.
    assert visible[0]["request_id"] == completed["request_id"]


def test_real_contract_does_not_schedule_market_indices(automatic, monkeypatch):
    from datetime import date

    monkeypatch.setattr(planning, "target_day", lambda: date(2026, 9, 9))
    with automatic._engine.begin() as c:
        c.execute(
            text(
                "UPDATE data_sync_contracts SET planned_revision=0, "
                'details=\'{"list_date":"20260901","delist_date":"20260908","last_ddate":"20260908"}\''
            )
        )
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        rows = (
            c.execute(text("SELECT scope,parameters FROM data_sync_jobs WHERE dataset='index'"))
            .mappings()
            .all()
        )
    assert rows == []
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_sync_jobs WHERE dataset='index'")) == len(
            rows
        )


@pytest.mark.parametrize(
    "message,label",
    [
        ("抱歉，您没有接口(ft_limit)访问权限", "权限不足"),
        ("必填参数, ts_code", "参数或范围"),
    ],
)
def test_observed_provider_rejections_are_classified_without_raw_message(message, label):
    with pytest.raises(acquisition.DownloadError, match=label) as caught:
        acquisition.decode(json.dumps({"code": 40203, "msg": message + TOKEN}).encode())
    assert TOKEN not in str(caught.value)


def test_only_expired_contracts_are_planned_without_truncating_lifetime(automatic, monkeypatch):
    from datetime import date

    monkeypatch.setattr(planning, "target_day", lambda: date(2012, 2, 2))
    with automatic._engine.begin() as c:
        c.execute(text("DELETE FROM data_contract_collections"))
        c.execute(text("DELETE FROM data_sync_contracts"))
        for code, kind, begin, end in [
            ("AL1112.SHF", "1", "20100101", "20111215"),
            ("AL1201.SHF", "1", "20100101", "20120115"),
            ("AL1202.SHF", "1", "20110101", "20120215"),
            ("AL.SHF", "2", "20100101", ""),
        ]:
            c.execute(
                text(
                    "INSERT INTO data_sync_contracts(ts_code,exchange,product,kind,details) "
                    "VALUES(:code,'SHFE','AL',:kind,CAST(:details AS jsonb))"
                ),
                {
                    "code": code,
                    "kind": kind,
                    "details": json.dumps(
                        {
                            "list_date": begin,
                            "delist_date": end,
                            "last_ddate": end,
                            "d_mode_desc": "实物交割",
                        }
                    ),
                },
            )
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        rows = (
            c.execute(text("SELECT dataset,scope,start_at,end_at FROM data_sync_jobs"))
            .mappings()
            .all()
        )
        assert rows
        assert min(r["start_at"] for r in rows) == "2010-01-01"
        assert any(r["scope"] == "AL1201.SHF" for r in rows)
        assert not any(r["scope"] == "AL1112.SHF" for r in rows)
        assert not any(r["scope"] == "AL1202.SHF" for r in rows)
        assert {"calendar", "daily", "1min", "holdings", "warehouse"} <= {
            r["dataset"] for r in rows
        }
        assert (
            c.scalar(
                text("SELECT count(*) FROM data_sync_contracts WHERE planning_error IS NOT NULL")
            )
            == 0
        )


def test_zero_volume_blocked_source_reprocesses_without_downloading(automatic, monkeypatch):
    from uuid import UUID

    from northstar_quant.data_management.tushare import quality, reprocessing

    request_id = UUID(pending(automatic))
    data = json.loads(response())
    row = data["data"]["items"][0]
    fields = data["data"]["fields"]
    for name, value in {"open": None, "high": None, "low": None, "vol": 0, "amount": 0}.items():
        row[fields.index(name)] = value
    raw = json.dumps(data).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *a: raw)
    original_normalize = jobs.normalize
    monkeypatch.setattr(
        jobs,
        "normalize",
        lambda *a: (_ for _ in ()).throw(quality.InvalidResponse("previous strict price rule")),
    )
    blocked = jobs.process_next(automatic)
    assert blocked["status"] == "BLOCKED"
    source = UUID(blocked["reprocess_source"]["generation"])
    monkeypatch.setattr(jobs, "normalize", original_normalize)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: pytest.fail("must reuse retained bytes"))
    reprocessing.enqueue(automatic._engine, request_id=request_id, source_generation=source)
    result = jobs.process_next(automatic)
    assert result["status"] == "VALIDATED"
    assert result["attempts"] == blocked["attempts"]
    assert result["attempts_detail"][0]["parent_generation"] == str(source)
    with automatic._engine.connect() as connection:
        receipt = connection.execute(text("SELECT * FROM data_sync_receipts")).mappings().one()
    assert automatic._files.read(receipt["source_hash"], receipt["source_bytes"]) == raw
    snapshot = publication.read_snapshot(receipt["manifest_hash"], receipt["manifest_bytes"])
    assert snapshot["quality"]["zero_volume_rows"] == 1


def test_mixed_response_retains_evidence_without_partial_publication(automatic, monkeypatch):
    pending(automatic, end="2026-09-02")
    data = json.loads(response())
    bad = list(data["data"]["items"][0])
    bad[1] = "20260902"
    bad[5] = None
    data["data"]["items"].append(bad)
    raw = json.dumps(data).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *a: raw)
    result = jobs.process_next(automatic)
    assert result["status"] == "BLOCKED"
    assert result["receipt_id"] is None
    assert result["attempts_detail"][0]["quality"]["issues"][0]["row_number"] == 2
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 0
        assert c.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 0
        attempt = c.execute(text("SELECT * FROM data_sync_attempts")).mappings().one()
    assert automatic._files.read(attempt["source_hash"], attempt["source_bytes"]) == raw


def test_provider_rate_reply_cools_whole_api_without_blocking_daily(automatic, monkeypatch):
    with automatic._engine.begin() as c:
        for dataset in ("1min", "15min"):
            planning.enqueue(
                c, dataset, "RB2610.SHF", {"freq": dataset}, "2012-01-01", "2012-01-01"
            )
    calls = []

    def fetch(api, *_):
        calls.append(api)
        if api == "ft_mins":
            raise acquisition.DownloadError(
                "Tushare 限频，等待退避重试", retry=True, rate_limited=True
            )
        return response()

    monkeypatch.setattr(acquisition, "fetch", fetch)
    result = jobs.process_next(automatic)
    assert result["status"] == "WAITING"
    pending(automatic)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_settings SET next_request_at=now()"))
    result = jobs.process_next(automatic)
    assert result["dataset"] == "daily" and result["status"] == "VALIDATED"
    assert calls == ["ft_mins", "fut_daily"]


def test_historical_candidate_preempts_existing_newer_collection(automatic, monkeypatch):
    from datetime import date

    from northstar_quant.data_management.tushare.scheduling import choose

    monkeypatch.setattr(planning, "target_day", lambda: date(2026, 9, 14))
    pending(automatic)
    with automatic._engine.begin() as c:
        for code, last in [("AL1202.SHF", "20120215"), ("AL1201.SHF", "20120115")]:
            c.execute(
                text("""INSERT INTO data_sync_contracts
                (ts_code,exchange,product,kind,details) VALUES(:code,'SHFE','AL','1',
                CAST(:details AS jsonb))"""),
                dict(
                    code=code,
                    details=json.dumps(
                        dict(list_date="20110101", delist_date=last, last_ddate=last)
                    ),
                ),
            )
    with automatic._engine.begin() as c:
        assert choose(c, download_ready=True) is None
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        assert c.scalar(text("SELECT min(end_date) FROM data_contract_collections")) == date(
            2012, 1, 15
        )
        row = choose(c, download_ready=True)
        owners = list(
            c.scalars(
                text("SELECT scope FROM data_contract_requests WHERE request_id=:id"),
                dict(id=row["request_id"]),
            )
        )
        assert "AL1201.SHF" in owners
        # Planning another candidate cannot create a flood before the oldest is collected.
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_contract_collections")) == 2


def test_queue_will_not_download_after_metadata_becomes_unknown(automatic):
    from northstar_quant.data_management.tushare.scheduling import choose

    pending(automatic)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_contracts SET details=details-'last_ddate'"))
        assert choose(c, download_ready=True) is None
        assert c.scalar(text("SELECT status FROM data_sync_jobs")) == "PENDING"


def test_malformed_unplanned_date_cannot_starve_valid_download(automatic):
    from northstar_quant.data_management.tushare.scheduling import choose

    pending(automatic)
    with automatic._engine.begin() as c:
        c.execute(
            text("""INSERT INTO data_sync_contracts
            (ts_code,exchange,product,kind,details) VALUES('AL1201.SHF','SHFE','AL','1',
            '{"list_date":"20110101","delist_date":"20120101unknown"}')""")
        )
        assert choose(c, download_ready=True)["scope"] == "RB2610.SHF"
