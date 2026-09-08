"""Read-only runtime observations, never trading readiness or storage repair."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from northstar_quant.data_management.library import DataLibrary


def observe(engine: Engine, library: DataLibrary) -> dict[str, object]:
    started = monotonic()
    database = "UNAVAILABLE"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database = "REACHABLE"
    except SQLAlchemyError:
        pass  # Never expose connection strings or database exception parameters.
    try:
        storage = library.storage_capacity()
    except OSError:
        storage = {"status": "UNAVAILABLE", "free_bytes": None, "free_inodes": None}
    return {
        "status": "OK" if database == "REACHABLE" and storage["status"] == "OK" else "DEGRADED",
        "observed_at": datetime.now(UTC).isoformat(),
        "duration_ms": round((monotonic() - started) * 1000),
        "database": {"status": database, "disk_capacity": "UNKNOWN"},
        "source_filesystem": storage,
        "scope": "DATABASE_REACHABILITY_AND_SOURCE_FILESYSTEM_ONLY",
    }
