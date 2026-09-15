"""Preserve retained facts and activate restoration only after evidence verification."""

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine

from northstar_quant.apps.storage import initialize_database
from northstar_quant.data_management.backup import backup, restore
from northstar_quant.data_management.files import SourceFiles


@contextmanager
def _empty_restore_database(source: Engine) -> Iterator[Engine]:
    """Own one disposable restore target; never clear or replace the source DB."""

    if not all(shutil.which(name) for name in ("pg_dump", "pg_restore")):
        pytest.skip("PostgreSQL client tools are required for joint restore acceptance")
    name = "northstar_quant_restore_test_" + uuid4().hex
    quoted = source.dialect.identifier_preparer.quote(name)
    with source.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(f"CREATE DATABASE {quoted} TEMPLATE template0 ENCODING 'UTF8'")
    target = create_engine(source.url.set(database=name))
    try:
        yield target
    finally:
        target.dispose()
        with source.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE {quoted}")


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
                "code_revision": "0" * 64,
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


def test_owned_data_hub_restore_does_not_require_other_application_tables(
    postgres_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import inspect

    monkeypatch.setenv("NORTHSTAR_DATABASE_OWNER", "data_hub")
    with _empty_restore_database(postgres_engine) as source:
        initialize_database(source)
        destination = tmp_path / "owned-backup"
        backup(source, SourceFiles(tmp_path / "owned-sources"), destination)
        with _empty_restore_database(postgres_engine) as target:
            result = restore(target, tmp_path / "owned-restored", destination)
            assert result["owner"] == "data_hub"
            assert result["status"] == "restored"
            assert not (tmp_path / "owned-restored/.restore-incomplete").exists()
            assert not any(
                name.startswith(("broker_", "research_"))
                for name in inspect(target).get_table_names()
            )


def test_data_hub_init_rejects_missing_current_tables_instead_of_upgrading_facts(
    postgres_engine: Engine,
) -> None:
    from sqlalchemy import inspect

    with _empty_restore_database(postgres_engine) as source:
        initialize_database(source, owner="data_hub")
        with source.begin() as connection:
            connection.exec_driver_sql("DROP TABLE data_compactions")
        with pytest.raises(ValueError, match="current initialized schema"):
            initialize_database(source, owner="data_hub")
        assert "data_compactions" not in inspect(source).get_table_names()
        with pytest.raises(ValueError, match="unknown database owner"):
            initialize_database(source, owner="all")
