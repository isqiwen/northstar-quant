"""Immutable contract-file identity and fail-closed whole-contract publication."""

import json
import zipfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

from northstar_quant.data_management.contract_data.lifecycle import completed
from northstar_quant.data_management.contract_data.packages import publish, write_package
from northstar_quant.data_management.files import SourceFiles
from tests.data_management import test_tushare

automatic = test_tushare.automatic


@pytest.mark.parametrize(
    "kind,end",
    [("2", "20260901"), ("1", "20260914"), ("1", "20260915"), ("1", None), ("1", "20261301")],
)
def test_no_active_continuous_or_unknown_contract_is_eligible(kind, end):
    with pytest.raises(ValueError):
        completed(
            dict(kind=kind, details=dict(list_date="20200101", delist_date=end)),
            today=date(2026, 9, 14),
        )


def test_retired_contract_keeps_full_lifetime_before_old_floor():
    value = completed(
        dict(
            kind="1",
            details=dict(list_date="20090101", delist_date="20110101", last_ddate="20110105"),
        ),
        today=date(2026, 9, 14),
    )
    assert value.start == date(2009, 1, 1)
    assert value.end == date(2011, 1, 1)


def test_package_cannot_publish_with_unknown_completeness(automatic, monkeypatch):
    with automatic._engine.begin() as c:
        c.execute(
            text("""UPDATE data_sync_contracts SET details=
            '{"list_date":"20260901","delist_date":"20260902","last_ddate":"20260903"}'""")
        )
    with pytest.raises(ValueError, match="禁止发布"):
        publish(automatic._engine, "RB2610.SHF")
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_contract_publications")) == 0
    import os

    assert list(Path(os.environ["NORTHSTAR_MARKET_DIR"]).rglob("*.zip")) == []


def test_one_deterministic_package_preserves_input_bytes_and_refuses_corruption(tmp_path):
    source = SourceFiles(tmp_path / "private")
    inputs = {}
    for role in ("source", "manifest", "parquet"):
        item = source.store((role + " synthetic test bytes").encode())
        inputs.update({f"{role}_hash": item.content_hash, f"{role}_bytes": item.byte_count})
    manifest = dict(exchange="SHFE", product="RB", scope="RB2501.SHF", inputs=[inputs])
    root = tmp_path / "market"
    artifact = write_package(root, manifest, source)
    path = root / artifact["path"]
    assert path.parent == root / "SHFE" / "RB" / "RB2501.SHF"
    before = path.read_bytes()
    assert write_package(root, manifest, source) == artifact
    assert path.read_bytes() == before
    with zipfile.ZipFile(path) as package:
        assert json.loads(package.read("manifest.json")) == manifest
        for role in ("source", "manifest", "parquet"):
            suffix = "parquet" if role == "parquet" else "json"
            assert (
                package.read(f"{role}/{inputs[role + '_hash']}.{suffix}")
                == (role + " synthetic test bytes").encode()
            )
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="内容不一致"):
        write_package(root, manifest, source)
    assert path.read_bytes() == b"corrupt"


def test_package_path_cannot_escape_through_metadata_or_symlinks(tmp_path):
    source = SourceFiles(tmp_path / "private")
    root = tmp_path / "market"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "SHFE").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        write_package(
            root, dict(exchange="SHFE", product="RB", scope="RB2501.SHF", inputs=[]), source
        )
    with pytest.raises(ValueError, match="非法路径"):
        write_package(
            root, dict(exchange="../outside", product="RB", scope="RB2501.SHF", inputs=[]), source
        )
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("protected", [False, True])
def test_rejected_raw_cleanup_preserves_fixed_receipts(automatic, monkeypatch, protected):
    from uuid import UUID

    from northstar_quant.data_management.contract_data.retention import release_rejected
    from northstar_quant.data_management.tushare import acquisition, jobs, reprocessing

    request = UUID(test_tushare.pending(automatic))
    raw = json.loads(test_tushare.response())
    if not protected:
        raw["data"]["items"][0][raw["data"]["fields"].index("vol")] = -1
    monkeypatch.setattr(acquisition, "fetch", lambda *_: json.dumps(raw).encode())
    result = jobs.process_next(automatic)
    source = UUID(result["reprocess_source"]["generation"])
    with automatic._engine.begin() as c:
        original = c.execute(text("SELECT * FROM data_sources")).mappings().one()
        c.execute(text("UPDATE data_contract_collections SET status='VERIFYING'"))
    assert release_rejected(automatic._engine, automatic._files) == 0
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_contract_collections SET status='REJECTED'"))
    assert release_rejected(automatic._engine, automatic._files) == (0 if protected else 1)
    assert automatic._files.inspect(original["content_hash"], original["byte_count"]) == (
        "AVAILABLE" if protected else "MISSING"
    )
    if not protected:
        with pytest.raises(ValueError, match="已随拒绝合约清理"):
            reprocessing.enqueue(automatic._engine, request_id=request, source_generation=source)
    assert release_rejected(automatic._engine, automatic._files) == 0


@pytest.mark.parametrize(
    "pin_kind", ["backup", "another_contract", "source_gate", "unknown_lifecycle"]
)
def test_rejected_cleanup_respects_other_owners_and_maintenance(automatic, monkeypatch, pin_kind):
    from northstar_quant.data_management.contract_data.retention import release_rejected
    from northstar_quant.data_management.maintenance import library_write
    from northstar_quant.data_management.tushare import acquisition, jobs
    from tests.data_management.test_cleanup import pin

    test_tushare.pending(automatic)
    raw = json.loads(test_tushare.response())
    raw["data"]["items"][0][raw["data"]["fields"].index("vol")] = -1
    content = json.dumps(raw).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *_: content)
    jobs.process_next(automatic)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_contract_collections SET status='REJECTED'"))
        if pin_kind == "unknown_lifecycle":
            c.execute(text("UPDATE data_sync_contracts SET details=details-'last_ddate'"))
        if pin_kind == "another_contract":
            c.execute(
                text("""INSERT INTO data_sync_contracts
                (ts_code,exchange,product,kind,details)
                VALUES('RB2609.SHF','SHFE','RB','1','{}')""")
            )
            c.execute(
                text("""INSERT INTO data_contract_collections(scope,start_date,end_date)
                VALUES('RB2609.SHF','2026-09-01','2026-09-02')""")
            )
            c.execute(
                text("""INSERT INTO data_contract_requests
                SELECT 'RB2609.SHF',request_id FROM data_sync_jobs""")
            )
    if pin_kind == "backup":
        pin(automatic._engine, automatic._files.store(content))
    if pin_kind == "source_gate":
        with library_write(automatic._engine):
            assert release_rejected(automatic._engine, automatic._files) == 0
    else:
        assert release_rejected(automatic._engine, automatic._files) == 0
    assert automatic.verify_sources() == 1


@pytest.mark.parametrize(
    "delivery,state",
    [("20260915", "DELIVERING"), (None, "UNKNOWN"), ("20260901", "UNKNOWN")],
)
def test_last_trade_does_not_establish_business_completion(delivery, state):
    from northstar_quant.data_management.contract_data.lifecycle import describe

    contract = dict(
        kind="1",
        details=dict(
            list_date="20250101",
            delist_date="20260911",
            last_ddate=delivery,
        ),
    )
    assert describe(contract, today=date(2026, 9, 14))["lifecycle_status"] == state
    with pytest.raises(ValueError):
        completed(contract, today=date(2026, 9, 14))


def test_unknown_lifecycle_never_rejects_existing_collection(automatic):
    from northstar_quant.data_management.contract_data.processing import process_next

    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_contracts SET details=details-'last_ddate'"))
        c.execute(
            text("""UPDATE data_contract_collections SET status='VERIFYING',
            updated_at=now()-interval '2 minutes'""")
        )
    assert process_next(automatic._engine) == "RB2610.SHF"
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT status FROM data_contract_collections")) == "VERIFYING"


def test_cleanup_discovery_does_not_lock_collectors_and_rechecks_new_owner_state(
    automatic, monkeypatch
):
    from northstar_quant.data_management.contract_data import retention
    from northstar_quant.data_management.maintenance import library_write
    from northstar_quant.data_management.tushare import acquisition, jobs

    test_tushare.pending(automatic)
    raw = json.loads(test_tushare.response())
    raw["data"]["items"][0][raw["data"]["fields"].index("vol")] = -1
    monkeypatch.setattr(acquisition, "fetch", lambda *_: json.dumps(raw).encode())
    jobs.process_next(automatic)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_contract_collections SET status='REJECTED'"))
        source = c.execute(text("SELECT * FROM data_sources")).mappings().one()
    candidates = retention._candidates

    def concurrent_change(c, ids=None):
        result = candidates(c, ids)
        if ids is None:
            assert result
            # Another collector can enter during discovery. A state change made
            # then must prevent deletion once retention acquires its exclusive gate.
            with library_write(automatic._engine), automatic._engine.begin() as writer:
                writer.execute(text("UPDATE data_contract_collections SET status='VERIFYING'"))
        return result

    monkeypatch.setattr(retention, "_candidates", concurrent_change)
    assert retention.release_rejected(automatic._engine, automatic._files) == 0
    assert automatic._files.inspect(source["content_hash"], source["byte_count"]) == "AVAILABLE"
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_contract_source_releases")) == 0


def test_filtered_manifest_preserves_source_and_receipt_aliases(automatic, monkeypatch):
    from northstar_quant.data_management.library import manifest
    from northstar_quant.data_management.tushare import acquisition, jobs

    test_tushare.pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *_: test_tushare.response())
    jobs.process_next(automatic)
    with automatic._engine.connect() as c:
        full = manifest(c)
        assert len(full) >= 3
        for item in full:
            selected = manifest(c, content_hashes=[str(item["content_hash"])])
            assert selected == [r for r in full if r["content_hash"] == item["content_hash"]]
        assert manifest(c, content_hashes=[]) == []
