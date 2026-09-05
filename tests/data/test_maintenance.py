"""Preserve retained facts and activate restoration only after evidence verification."""

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from test_broker_baselines import saved_query

from northstar_quant.broker.baselines import BrokerBaselines
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import get_profile
from northstar_quant.data.files import SourceFiles
from northstar_quant.data.maintenance import backup, restore
from northstar_quant.db import initialize_database


@contextmanager
def _empty_restore_database(source: Engine) -> Iterator[Engine]:
    """Own one disposable restore target; never clear or replace the source DB."""

    if not all(shutil.which(name) for name in ("pg_dump", "pg_restore")):
        pytest.skip("PostgreSQL client tools are required for joint restore acceptance")
    name = "northstar_quant_restore_test_" + uuid4().hex
    quoted = source.dialect.identifier_preparer.quote(name)
    with source.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(f"CREATE DATABASE {quoted}")
    target = create_engine(source.url.set(database=name))
    try:
        yield target
    finally:
        target.dispose()
        with source.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE {quoted}")


def test_initialization_and_restore_keep_all_interrupted_query_evidence(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    saved = [
        records.begin(
            get_profile("simnow_dev").identity(),
            "123456",
            "rb2610",
            request_id=UUID(int=number),
        )
        for number in range(1, 102)
    ]
    baselines = BrokerBaselines(postgres_engine)
    baseline_id, check_id = uuid4(), uuid4()
    baseline = baselines.establish(saved_query(postgres_engine), request_id=baseline_id)
    comparison = baselines.compare(baseline_id, saved_query(postgres_engine), request_id=check_id)
    # Explicit initialization may add current Module tables, never rebind facts.
    initialize_database(postgres_engine)
    assert records.get(UUID(int=1)) == saved[0]
    assert records.get(UUID(int=101)) == saved[-1]
    assert baselines.get_baseline(baseline_id) == baseline
    assert baselines.get_check(check_id) == comparison
    with _empty_restore_database(postgres_engine) as target:
        backup(postgres_engine, SourceFiles(tmp_path / "sources"), tmp_path / "backup")
        result = restore(target, tmp_path / "restored", tmp_path / "backup")
        assert result["evidence"] == {
            "query_batches_count": 103,
            "pending_queries_count": 101,
            "baselines_count": 1,
            "checks_count": 1,
        }
        assert result["execution"] == "PAUSED"
        restored = BrokerRecords(target)
        assert restored.get(UUID(int=1)) == saved[0]
        assert restored.get(UUID(int=101)) == saved[-1]
        restored_baselines = BrokerBaselines(target)
        assert restored_baselines.get_baseline(baseline_id) == baseline
        assert restored_baselines.get_check(check_id) == comparison
        assert not (tmp_path / "restored/.restore-incomplete").exists()


def test_corrupt_query_evidence_keeps_restore_unactivated(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    del clean_database
    records = BrokerRecords(postgres_engine)
    for number in range(1, 102):
        records.begin(
            get_profile("simnow_dev").identity(),
            "123456",
            "rb2610",
            request_id=UUID(int=number),
        )
    # Simulate damaged backup content; this is fault injection, not an app write path.
    # The oldest query lies outside the workspace's newest-100 list.
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text("UPDATE broker_query_batches SET binding_hash = :digest WHERE batch_id = :id"),
            {"digest": "0" * 64, "id": UUID(int=1)},
        )
    with _empty_restore_database(postgres_engine) as target:
        backup(postgres_engine, SourceFiles(tmp_path / "sources"), tmp_path / "backup")
        with pytest.raises(ValueError, match="query identity no longer matches"):
            restore(target, tmp_path / "restored", tmp_path / "backup")
        assert (tmp_path / "restored/.restore-incomplete").is_file()


def test_restore_installs_new_module_tables_without_replacing_existing_facts(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    with _empty_restore_database(postgres_engine) as source:
        initialize_database(source)
        query_id = saved_query(source)
        original = BrokerRecords(source).get(query_id)
        assert BrokerBaselines(source).verify_all() == {"baselines_count": 0, "checks_count": 0}
        destination = tmp_path / "backup"
        document = backup(source, SourceFiles(tmp_path / "sources"), destination)
        # This isolated source models the same core baseline before these two
        # empty Module tables existed. No existing account fact is removed.
        with source.begin() as connection:
            connection.exec_driver_sql("DROP TABLE broker_baseline_checks")
            connection.exec_driver_sql("DROP TABLE broker_account_baselines")
        dump = destination / "database.dump"
        subprocess.run(
            [
                "pg_dump",
                "--no-password",
                f"--host={source.url.host}",
                f"--port={source.url.port or 5432}",
                f"--username={source.url.username}",
                f"--dbname={source.url.database}",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                f"--file={dump}",
            ],
            env=dict(os.environ, PGPASSWORD=source.url.password or ""),
            capture_output=True,
            check=True,
            timeout=60,
        )
        document["database_sha256"] = hashlib.sha256(dump.read_bytes()).hexdigest()
        (destination / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
        with _empty_restore_database(postgres_engine) as target:
            result = restore(target, tmp_path / "restored", destination)
            assert result["evidence"] == {
                "query_batches_count": 1,
                "pending_queries_count": 0,
                "baselines_count": 0,
                "checks_count": 0,
            }
            assert result["execution"] == "PAUSED"
            assert BrokerRecords(target).get(query_id) == original
            restored = BrokerBaselines(target)
            baseline = restored.establish(query_id, request_id=uuid4())
            assert baseline["source_batch_id"] == str(query_id)
            assert BrokerRecords(target).get(query_id) == original
            assert not (tmp_path / "restored/.restore-incomplete").exists()


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
