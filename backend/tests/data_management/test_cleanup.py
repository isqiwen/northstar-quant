"""Reference changes, backup pins and interruption never authorize deleting retained facts."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from northstar_quant.data_management import cleanup
from northstar_quant.data_management.tushare.publication import storage
from tests.data_management import test_exploration

published = test_exploration.published


def pin(engine, item):
    with engine.begin() as c:
        c.execute(
            text("""INSERT INTO data_backups
            VALUES(:id,now(),:hash,CAST(:refs AS jsonb))"""),
            {
                "id": uuid4(),
                "hash": "a" * 64,
                "refs": json.dumps([{"source_id": str(uuid4()), **item.to_dict()}]),
            },
        )


def test_cleanup_preserves_all_references_and_staging(published):
    library, _ = published
    archive = library._files
    orphan = archive.store(b"orphan-source")
    storage().store(b"orphan-published")
    protected = archive.store(b"old-backup-source")
    pin(library._engine, protected)
    staging = archive.root / "staging" / "unfinished"
    staging.write_bytes(b"unfinished")
    plan = cleanup.preview(library._engine, archive)
    assert plan["orphan_count"] == 2
    result = cleanup.execute(library._engine, archive, plan["plan_id"])
    assert result["status"] == "SUCCEEDED"
    assert len(result["result"]["removed"]) == 2
    assert cleanup.execute(library._engine, archive, plan["plan_id"]) == result
    assert archive.read(protected.content_hash, protected.byte_count) == b"old-backup-source"
    assert staging.read_bytes() == b"unfinished"
    assert archive.inspect(orphan.content_hash, orphan.byte_count) == "MISSING"
    assert cleanup.preview(library._engine, archive)["orphan_count"] == 0
    from northstar_quant.data_management.exploration.rows import read

    assert (
        read(
            library._engine,
            dataset="1min",
            scope="RB2610.SHF",
            start="2026-09-01",
            end="2026-09-03",
            receipt_ids=[],
        )["total"]
        == 6
    )


def test_changed_references_refuse_stale_deletion_plan(published):
    library, _ = published
    item = library._files.store(b"becomes-protected-after-preview")
    plan = cleanup.preview(library._engine, library._files)
    pin(library._engine, item)
    with pytest.raises(ValueError, match="引用已变化"):
        cleanup.execute(library._engine, library._files, plan["plan_id"])
    assert library._files.read(item.content_hash, item.byte_count)


def test_cleanup_interruption_is_recorded_and_not_retried(published, monkeypatch):
    library, _ = published
    item = library._files.store(b"orphan-interrupted")
    plan = cleanup.preview(library._engine, library._files)

    def fail(*args):
        raise OSError("synthetic unlink failure")

    monkeypatch.setattr(library._files, "remove_verified", fail)
    result = cleanup.execute(library._engine, library._files, plan["plan_id"])
    assert result["status"] == "FAILED"
    assert result["result"]["removed"] == []
    assert cleanup.execute(library._engine, library._files, plan["plan_id"]) == result
    assert library._files.read(item.content_hash, item.byte_count)
    with pytest.raises(Exception, match="immutable"):
        with library._engine.begin() as c:
            c.execute(text("DELETE FROM data_cleanup_receipts"))


def test_crash_requires_new_preview_before_any_retry(published, monkeypatch):
    library, _ = published
    archive = library._files
    archive.store(b"orphan-crash")
    plan = cleanup.preview(library._engine, archive)
    original = archive.remove_verified

    def crash(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(archive, "remove_verified", crash)
    with pytest.raises(KeyboardInterrupt):
        cleanup.execute(library._engine, archive, plan["plan_id"])
    assert cleanup.execute(library._engine, archive, plan["plan_id"])["status"] == "STARTED"
    fresh = cleanup.preview(library._engine, archive)
    assert fresh["plan_id"] != plan["plan_id"]
    monkeypatch.setattr(archive, "remove_verified", original)
    assert cleanup.execute(library._engine, archive, fresh["plan_id"])["status"] == "SUCCEEDED"
