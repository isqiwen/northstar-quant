"""One maintenance gate for source processing and consistent database/file backups."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, inspect, text

from northstar_quant.data.files import SourceFiles

_LIBRARY_LOCK = 0x4E535144415441


@contextmanager
def library_write(engine: Engine) -> Iterator[None]:
    """Hold shared admission until the complete bounded ingestion operation ends."""

    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(
            text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": _LIBRARY_LOCK}
        )
        yield


def initialize_maintenance(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS data_backups (
            backup_id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL,
            manifest_hash varchar(64) NOT NULL,
            source_references jsonb NOT NULL
        )
    """)


def _pg_command(engine: Engine, executable: str, *arguments: str) -> None:
    binary = shutil.which(executable)
    if binary is None:
        raise ValueError(f"{executable} is required; install PostgreSQL client tools")
    url = engine.url
    environment = dict(os.environ)
    if url.password is not None:
        environment["PGPASSWORD"] = url.password
    command = [
        binary,
        "--no-password",
        f"--host={url.host or 'localhost'}",
        f"--port={url.port or 5432}",
        f"--username={url.username or ''}",
        f"--dbname={url.database or ''}",
        *arguments,
    ]
    try:
        subprocess.run(command, env=environment, capture_output=True, check=True, timeout=300)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"{executable} exceeded the bounded maintenance window") from error
    except subprocess.CalledProcessError as error:
        # Client diagnostics can contain connection details or input content.
        raise ValueError(f"{executable} failed; no completed backup/restore is declared") from error


def _file_hash(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("backup content must be a regular file")
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_record(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    SourceFiles._sync(path.parent)


def backup(engine: Engine, files: SourceFiles, destination: Path) -> dict[str, object]:
    """Copy one exported database snapshot and exactly its immutable source references.

    Source processing is excluded by the shared maintenance gate. The exported
    PostgreSQL snapshot, not command timing, fixes all records and references.
    No source or published fact has a deletion operation; completed backup pins
    are also retained. This is not broker recovery or an automatic restore point.
    """

    from northstar_quant.data.library import manifest
    from northstar_quant.db import require_current_database
    from northstar_quant.runtime import implementation_hash

    require_current_database(engine)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("backup requires a new absolute destination directory")
    target = destination.resolve()
    if target.is_relative_to(files.root) or files.root.is_relative_to(target):
        raise ValueError("backup and live source storage must be separate directories")
    if shutil.which("pg_dump") is None:
        raise ValueError("pg_dump is required; install PostgreSQL client tools")
    target.mkdir(parents=True, mode=0o700)
    backup_id = str(uuid4())
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LIBRARY_LOCK})
            snapshot = connection.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            references = manifest(connection)
            archived = SourceFiles(
                target / "sources",
                max_total_bytes=files.max_total_bytes,
                min_free_bytes=files.min_free_bytes,
            )
            for item in references:
                archived.store(files.read(str(item["content_hash"]), cast(int, item["byte_count"])))
            dump = target / "database.dump"
            _pg_command(
                engine,
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                f"--snapshot={snapshot}",
                f"--file={dump}",
            )
            with dump.open("rb") as stream:
                os.fsync(stream.fileno())
            document = {
                "format": "northstar-current-data-backup",
                "backup_id": backup_id,
                "created_at": datetime.now(UTC).isoformat(),
                "implementation_hash": implementation_hash(),
                "baseline": connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one(),
                "database_sha256": _file_hash(dump),
                "sources": references,
                "deletion_enabled": False,
            }
            content = json.dumps(
                document, ensure_ascii=False, sort_keys=True, allow_nan=False
            ).encode()
            _write_record(target / "manifest.json", content)
            connection.execute(
                text("""INSERT INTO data_backups
                    (backup_id, created_at, manifest_hash, source_references)
                    VALUES (:id, :created, :hash, CAST(:sources AS jsonb))"""),
                {
                    "id": backup_id,
                    "created": document["created_at"],
                    "hash": hashlib.sha256(content).hexdigest(),
                    "sources": json.dumps(references),
                },
            )
    return document


def _read_manifest(directory: Path) -> dict[str, object]:
    path = directory / "manifest.json"
    if directory.is_symlink():
        raise ValueError("backup manifest is not a bounded regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > 20 * 1024 * 1024:
            raise ValueError("backup manifest is not a bounded regular file")
        content = stream.read(20 * 1024 * 1024 + 1)
    if len(content) != details.st_size or len(content) > 20 * 1024 * 1024:
        raise ValueError("backup manifest changed or exceeded its size limit")
    document = json.loads(content)
    fields = {
        "format",
        "backup_id",
        "created_at",
        "implementation_hash",
        "baseline",
        "database_sha256",
        "sources",
        "deletion_enabled",
    }
    if not isinstance(document, dict) or set(document) != fields:
        raise ValueError("backup manifest has an unsupported shape")
    if (
        document["format"] != "northstar-current-data-backup"
        or document["deletion_enabled"] is not False
    ):
        raise ValueError("backup does not describe the current retained-data model")
    UUID(document["backup_id"])
    if not isinstance(document["sources"], list):
        raise ValueError("backup source references are invalid")
    identities = set()
    for item in document["sources"]:
        if not isinstance(item, dict) or set(item) != {"source_id", "content_hash", "byte_count"}:
            raise ValueError("backup source reference has an unsupported shape")
        identity = UUID(item["source_id"])
        if identity in identities:
            raise ValueError("backup source identities must be unique")
        identities.add(identity)
    return cast(dict[str, object], document)


def restore(engine: Engine, source_root: Path, directory: Path) -> dict[str, object]:
    """Restore a trusted operator-selected backup into an empty database and new directory.

    No --clean, DROP, overwrite or compatibility path exists. All referenced bytes
    and the dump are checked before touching the target. Failed activation leaves
    a marker that the normal application entrypoints refuse to open.
    """

    from northstar_quant.broker.baselines import BrokerBaselines
    from northstar_quant.broker.ledger import BrokerLedger
    from northstar_quant.broker.records import BrokerRecords
    from northstar_quant.data.library import DataLibrary, manifest
    from northstar_quant.db import initialize_database, require_current_database

    if (
        not directory.is_absolute()
        or not source_root.is_absolute()
        or source_root.exists()
        or source_root.is_symlink()
    ):
        raise ValueError("restore needs an absolute backup and a new absolute source directory")
    target_root, backup_root = source_root.resolve(), directory.resolve()
    if target_root.is_relative_to(backup_root) or backup_root.is_relative_to(target_root):
        raise ValueError("restore target and backup must be separate directories")
    document = _read_manifest(directory)
    sources = cast(list[dict[str, object]], document["sources"])
    if _file_hash(directory / "database.dump") != document["database_sha256"]:
        raise ValueError("backup database digest does not match the completed manifest")
    archive_path = directory / "sources"
    if archive_path.is_symlink() or not all(
        (archive_path / part).is_dir() for part in ("objects", "staging")
    ):
        raise ValueError("backup source archive is missing")
    archive = SourceFiles(archive_path)
    for item in sources:
        archive.read(str(item["content_hash"]), int(cast(int, item["byte_count"])))
    if shutil.which("pg_restore") is None:
        raise ValueError("pg_restore is required; install PostgreSQL client tools")
    with engine.begin() as connection:
        schemas = set(inspect(connection).get_schema_names()) - {"information_schema", "public"}
        if schemas or inspect(connection).get_table_names() or inspect(connection).get_view_names():
            raise ValueError("restore target database must be empty; existing data is never reset")
    target = SourceFiles(source_root)
    _write_record(
        target.root / ".restore-incomplete", b"Restore has not passed activation checks.\n"
    )
    for item in sources:
        target.store(archive.read(str(item["content_hash"]), int(cast(int, item["byte_count"]))))
    _pg_command(
        engine,
        "pg_restore",
        "--no-owner",
        "--no-acl",
        "--exit-on-error",
        "--single-transaction",
        str(directory / "database.dump"),
    )
    # A matching core baseline may predate newly added Module tables. Use the
    # ordinary explicit initializer; it preserves facts and rejects retired cores.
    initialize_database(engine)
    require_current_database(engine)
    library = DataLibrary(engine, target)
    with engine.connect() as connection:
        restored = manifest(connection)
        baseline = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        snapshot_ids = (
            connection.execute(
                text(
                    "SELECT DISTINCT snapshot_id FROM data_processing_attempts "
                    "WHERE snapshot_id IS NOT NULL"
                )
            )
            .scalars()
            .all()
        )
    if restored != sources or baseline != document["baseline"]:
        raise ValueError("restored source relationships or current baseline differ from backup")
    # Opening all published snapshots checks original mappings, quality pins,
    # source hashes and actual archive bytes without recomputing historical runs.
    for snapshot_id in snapshot_ids:
        library.load_dataset(snapshot_id)
    audit = library.reconcile()
    records = BrokerRecords(engine)
    query_batches_count = pending_queries_count = 0
    # A restored database is not active yet. Stream every identity, not the
    # workspace's bounded recent list, and let the owning Module verify facts.
    # PENDING remains interrupted evidence: get() never reconnects or resumes it.
    with engine.connect().execution_options(yield_per=100) as connection:
        query_ids = connection.execute(
            text("SELECT batch_id FROM broker_query_batches ORDER BY batch_id")
        ).scalars()
        for query_id in query_ids:
            query = records.get(query_id)
            query_batches_count += 1
            pending_queries_count += query["status"] == "PENDING"
    baselines = BrokerBaselines(engine).verify_all()
    positions = BrokerLedger(engine).verify_all()
    evidence = {
        "query_batches_count": query_batches_count,
        "pending_queries_count": pending_queries_count,
        "baselines_count": baselines["baselines_count"],
        "checks_count": baselines["checks_count"],
        "position_entries_count": positions["position_entries_count"],
        "position_checks_count": positions["position_checks_count"],
        "order_checks_count": positions["order_checks_count"],
    }
    (target.root / ".restore-incomplete").unlink()
    SourceFiles._sync(target.root)
    return {
        "status": "restored",
        "backup_id": document["backup_id"],
        "source_count": len(sources),
        "audit": audit,
        "evidence": evidence,
        "execution": "PAUSED",
        "scope": (
            "retained sources, data/research, broker baseline, position and order evidence; "
            "current account reconciliation and execution recovery are not established"
        ),
    }
