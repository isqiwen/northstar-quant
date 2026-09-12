"""Consistent local Live snapshots and explicit restore into an empty instance."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Engine, inspect

from northstar_quant import code_revision
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary, manifest
from northstar_quant.live.recovery import verify
from northstar_quant.live.storage import open_store, require_current
from northstar_quant.persistence.backup_files import file_hash, read_record
from northstar_quant.persistence.locks import FileLock


def backup(engine: Engine, files: SourceFiles, destination: Path) -> dict[str, Any]:
    require_current(engine)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("Live backup requires a new absolute directory")
    database = Path(str(engine.url.database))
    if (
        destination.is_relative_to(files.root)
        or files.root.is_relative_to(destination)
        or database.is_relative_to(destination)
    ):
        raise ValueError("Live backup must be separate from active storage")
    destination.mkdir(parents=True, mode=0o700)
    saved = destination / "database.sqlite"
    saved.touch(mode=0o600)
    with (
        closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(saved)) as target,
    ):
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Live backup integrity failure")
    frozen = open_store(saved)
    try:
        require_current(frozen)
        with frozen.connect() as connection:
            references = manifest(connection)
        archive = SourceFiles(destination / "sources", max_total_bytes=files.max_total_bytes)
        archive.store_many(
            files.read(str(item["content_hash"]), cast(int, item["byte_count"]))
            for item in references
        )
        evidence = verify(frozen, DataLibrary(frozen, archive))
    finally:
        frozen.dispose()
    document = dict(
        format="northstar-live-sqlite-backup",
        backup_id=str(uuid4()),
        code_revision=code_revision(),
        database_sha256=file_hash(saved),
        sources=references,
    )
    path = destination / "manifest.json"
    with path.open("x", encoding="utf-8") as output:
        json.dump(document, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    with saved.open("rb") as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {**document, "evidence": evidence}


def restore(engine: Engine, source_root: Path, directory: Path) -> dict[str, Any]:
    if (
        engine.dialect.name != "sqlite"
        or source_root.exists()
        or source_root.is_symlink()
        or not source_root.is_absolute()
    ):
        raise ValueError("Live restore requires empty local SQLite and a new source directory")
    if directory.is_symlink() or not directory.is_absolute():
        raise ValueError("Live backup must be an absolute non-symlink directory")
    database = Path(str(engine.url.database))
    if (
        source_root.is_relative_to(directory)
        or directory.is_relative_to(source_root)
        or database.is_relative_to(directory)
    ):
        raise ValueError("Live restore target and backup must be separate directories")
    document = json.loads(read_record(directory / "manifest.json"))
    if not isinstance(document, dict) or set(document) != {
        "format",
        "backup_id",
        "code_revision",
        "database_sha256",
        "sources",
    }:
        raise ValueError("Live backup manifest has an unsupported shape")
    UUID(document["backup_id"])
    saved = directory / "database.sqlite"
    if (
        saved.is_symlink()
        or document.get("format") != "northstar-live-sqlite-backup"
        or file_hash(saved) != document.get("database_sha256")
    ):
        raise ValueError("Live backup identity mismatch")
    archive_path = directory / "sources"
    if archive_path.is_symlink() or not all(
        (archive_path / name).is_dir() for name in ("objects", "staging")
    ):
        raise ValueError("Live backup source archive is missing")
    original = SourceFiles(archive_path)
    frozen = open_store(saved)
    try:
        require_current(frozen)
        with frozen.connect() as connection:
            references = manifest(connection)
        if references != document["sources"]:
            raise ValueError("Live backup source references differ from database")
        for item in references:
            original.read(str(item["content_hash"]), cast(int, item["byte_count"]))
        evidence = verify(frozen, DataLibrary(frozen, original))
    finally:
        frozen.dispose()
    with closing(FileLock(database)):
        with engine.connect() as connection:
            if inspect(connection).get_table_names():
                raise ValueError("Live restore never overwrites existing facts")
        target_files = SourceFiles(source_root)
        target_files.store_many(
            original.read(str(item["content_hash"]), cast(int, item["byte_count"]))
            for item in references
        )
        engine.dispose()
        with (
            closing(sqlite3.connect(saved.as_uri() + "?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(database)) as target,
        ):
            source.backup(target)
        require_current(engine)
    return {
        "backup_id": document["backup_id"],
        "status": "restored",
        "execution": "RECONCILIATION_REQUIRED",
        "evidence": evidence,
    }
