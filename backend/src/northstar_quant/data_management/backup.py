"""Compose current same-domain database/file backup and owner-verified recovery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Engine, inspect, text

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.maintenance import freeze_sources, record_backup
from northstar_quant.persistence.backup_files import file_hash, read_record, write_record


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


def backup(engine: Engine, files: SourceFiles, destination: Path) -> dict[str, object]:
    """Copy one exported database snapshot and exactly its immutable source references.

    Source processing is excluded by the shared maintenance gate. The exported
    PostgreSQL snapshot, not command timing, fixes all records and references.
    No source or published fact has a deletion operation; completed backup pins
    are also retained. This is not broker recovery or an automatic restore point.
    """

    from northstar_quant import code_revision
    from northstar_quant.data_management.db.store import require_current
    from northstar_quant.data_management.library import manifest

    require_current(engine)
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
            freeze_sources(connection)
            snapshot = connection.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            references = manifest(connection)
            archived = SourceFiles(
                target / "sources",
                max_total_bytes=files.max_total_bytes,
                min_free_bytes=files.min_free_bytes,
            )
            archived.store_many(
                files.read(str(item["content_hash"]), cast(int, item["byte_count"]))
                for item in references
            )
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
                "code_revision": code_revision(),
                "baseline": connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one(),
                "database_sha256": file_hash(dump),
                "sources": references,
                "deletion_enabled": False,
            }
            content = json.dumps(
                document, ensure_ascii=False, sort_keys=True, allow_nan=False
            ).encode()
            write_record(target / "manifest.json", content)
            record_backup(connection, document, content)
    return document


def _read_manifest(directory: Path) -> dict[str, object]:
    path = directory / "manifest.json"
    if directory.is_symlink():
        raise ValueError("backup manifest is not a bounded regular file")
    content = read_record(path)
    document = json.loads(content)
    fields = {
        "format",
        "backup_id",
        "created_at",
        "code_revision",
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


def restore(
    engine: Engine, source_root: Path, directory: Path, *, storage_identity: str | None = None
) -> dict[str, object]:
    """Restore a trusted operator-selected backup into an empty database and new directory.

    No --clean, DROP, overwrite or compatibility path exists. All referenced bytes
    and the dump are checked before touching the target. Failed activation leaves
    a marker that the normal application entrypoints refuse to open.
    """

    from northstar_quant.data_management.db.store import require_current
    from northstar_quant.data_management.library import DataLibrary, manifest

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
    if file_hash(directory / "database.dump") != document["database_sha256"]:
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
    if storage_identity is not None:
        from northstar_quant.data_management.storage_identity import initialize

        source_root.mkdir(parents=True, mode=0o700)
        initialize(source_root, storage_identity)
    target = SourceFiles(source_root)
    write_record(
        target.root / ".restore-incomplete", b"Restore has not passed activation checks.\n"
    )
    target.store_many(
        archive.read(str(item["content_hash"]), int(cast(int, item["byte_count"])))
        for item in sources
    )
    _pg_command(
        engine,
        "pg_restore",
        "--no-owner",
        "--no-acl",
        "--exit-on-error",
        "--single-transaction",
        str(directory / "database.dump"),
    )
    require_current(engine)
    library = DataLibrary(engine, target)
    # Failed processing still owns admitted sources. Verify their links to the
    # retained broker prefix too, not only archives reached by publications.
    library.verify_sources()
    from northstar_quant.data_management.tushare.retention import restore_publications

    restore_publications(engine, target)
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
    (target.root / ".restore-incomplete").unlink()
    SourceFiles._sync(target.root)
    return {
        "status": "restored",
        "owner": "data_hub",
        "backup_id": document["backup_id"],
        "source_count": len(sources),
        "audit": audit,
        "scope": "Data Hub database, retained sources and fixed publications",
    }
