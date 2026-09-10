"""Consistent local Live snapshots and explicit restore into an empty instance."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Engine, inspect

from northstar_quant import code_revision
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import manifest
from northstar_quant.live.storage import open_store, require_current
from northstar_quant.persistence.locks import FileLock


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(saved)) as target:
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
    finally:
        frozen.dispose()
    document = dict(
        format="northstar-live-sqlite-backup",
        backup_id=str(uuid4()),
        code_revision=code_revision(),
        database_sha256=_hash(saved),
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
    return document


def restore(engine: Engine, source_root: Path, directory: Path) -> dict[str, Any]:
    if engine.dialect.name != "sqlite" or source_root.exists() or not source_root.is_absolute():
        raise ValueError("Live restore requires empty local SQLite and a new source directory")
    if directory.is_symlink() or not directory.is_absolute():
        raise ValueError("Live backup must be an absolute non-symlink directory")
    document = json.loads((directory / "manifest.json").read_text())
    saved = directory / "database.sqlite"
    if (
        saved.is_symlink()
        or document.get("format") != "northstar-live-sqlite-backup"
        or _hash(saved) != document.get("database_sha256")
    ):
        raise ValueError("Live backup identity mismatch")
    original = SourceFiles(directory / "sources")
    frozen = open_store(saved)
    try:
        require_current(frozen)
        with frozen.connect() as connection:
            references = manifest(connection)
        if references != document["sources"]:
            raise ValueError("Live backup source references differ from database")
        for item in references:
            original.read(str(item["content_hash"]), cast(int, item["byte_count"]))
    finally:
        frozen.dispose()
    database = Path(str(engine.url.database))
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
            closing(sqlite3.connect(saved)) as source,
            closing(sqlite3.connect(database)) as target,
        ):
            source.backup(target)
        require_current(engine)
    return {
        "backup_id": document["backup_id"],
        "status": "restored",
        "execution": "RECONCILIATION_REQUIRED",
    }
