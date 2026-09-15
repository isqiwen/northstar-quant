"""Read-only runtime observations, never trading readiness or storage repair."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from northstar_quant.data_management.library import DataLibrary


def _database_storage(engine: Engine, reachable: bool) -> dict[str, object]:
    """Inspect the actual SQLite volume without a checkpoint, write or repair."""
    if engine.dialect.name != "sqlite":
        return {"disk_capacity": "UNKNOWN"}
    unavailable: dict[str, object] = {
        "disk_capacity": "UNAVAILABLE",
        "free_bytes": None,
        "free_inodes": None,
        "database_bytes": None,
        "wal_bytes": None,
    }
    if not reachable or not engine.url.database:
        return unavailable
    path = Path(engine.url.database)
    try:
        size = path.stat().st_size
        stats = os.statvfs(path.parent)
        free_bytes = stats.f_bavail * stats.f_frsize
        free_inodes = stats.f_favail if stats.f_files > 0 and stats.f_favail >= 0 else None
        try:
            wal_size = Path(str(path) + "-wal").stat().st_size
        except FileNotFoundError:
            wal_size = 0  # SQLite may remove its WAL after the last connection closes.
    except OSError:
        return unavailable
    return {
        "disk_capacity": "FULL" if free_bytes == 0 or free_inodes == 0 else "OBSERVED",
        "free_bytes": free_bytes,
        "free_inodes": free_inodes,
        "database_bytes": size,
        "wal_bytes": wal_size,
    }


def observe(engine: Engine, library: DataLibrary) -> dict[str, object]:
    started = monotonic()
    database = "UNAVAILABLE"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database = "REACHABLE"
    except (SQLAlchemyError, OSError, ValueError):
        pass  # Never expose connection strings or database exception parameters.
    capacity = _database_storage(engine, database == "REACHABLE")
    try:
        storage = library.storage_capacity()
    except (OSError, ValueError):
        storage = {"status": "UNAVAILABLE", "free_bytes": None, "free_inodes": None}
    return {
        "status": "OK"
        if (
            database == "REACHABLE"
            and storage["status"] == "OK"
            and capacity["disk_capacity"] not in {"FULL", "UNAVAILABLE"}
        )
        else "DEGRADED",
        "observed_at": datetime.now(UTC).isoformat(),
        "duration_ms": round((monotonic() - started) * 1000),
        "database": {"status": database, **capacity},
        "source_filesystem": storage,
        "scope": "DATABASE_AND_LOCAL_STORAGE_OBSERVATIONS",
    }
