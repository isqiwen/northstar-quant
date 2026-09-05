"""Reject unsafe restoration inputs before touching any database or source target."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from northstar_quant.data.files import SourceFiles
from northstar_quant.data.maintenance import restore


def test_restore_never_writes_inside_backup_and_manifest_cannot_block_on_fifo(
    tmp_path: Path,
) -> None:
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:1/northstar_quant_test")
    backup = tmp_path / "backup"
    backup.mkdir()
    try:
        with pytest.raises(ValueError, match="separate directories"):
            restore(engine, backup / "sources/staging/restored", backup)
        assert list(backup.iterdir()) == []
        os.mkfifo(backup / "manifest.json")
        with pytest.raises(ValueError, match="bounded regular file"):
            restore(engine, tmp_path / "restored", backup)
        assert not (tmp_path / "restored").exists()
    finally:
        engine.dispose()


def test_restore_requires_self_contained_bytes_not_symlink_to_live_archive(tmp_path: Path) -> None:
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:1/northstar_quant_test")
    backup = tmp_path / "backup"
    backup.mkdir()
    live = SourceFiles(tmp_path / "live")
    (backup / "sources").symlink_to(live.root, target_is_directory=True)
    (backup / "database.dump").write_bytes(b"preflight must reject before opening database")
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                "format": "northstar-current-data-backup",
                "backup_id": str(uuid4()),
                "created_at": "2026-09-05T00:00:00Z",
                "implementation_hash": "0" * 64,
                "baseline": "20260905_04",
                "sources": [],
                "deletion_enabled": False,
                "database_sha256": hashlib.sha256(
                    (backup / "database.dump").read_bytes()
                ).hexdigest(),
            }
        )
    )
    try:
        with pytest.raises(ValueError, match="source archive is missing"):
            restore(engine, tmp_path / "restored", backup)
        assert not (tmp_path / "restored").exists()
        assert live.inventory() == []
    finally:
        engine.dispose()
