"""Data-owned admission gate and backup source-reference pins."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, text

from northstar_quant.live.storage import KernelLock

_LIBRARY_LOCK = 0x4E535144415441


@contextmanager
def library_write(engine: Engine) -> Iterator[None]:
    """Hold shared admission until the complete bounded ingestion operation ends."""

    if engine.dialect.name == "sqlite":
        with closing(KernelLock(Path(str(engine.url.database) + ".archive"))):
            yield
        return
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


def freeze_sources(connection: Connection) -> None:
    """Exclude source processing for the application's exported snapshot transaction."""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LIBRARY_LOCK})


def record_backup(connection: Connection, document: dict[str, Any], content: bytes) -> None:
    """Pin the source references in the same transaction as the completed backup record."""
    connection.execute(
        text("""INSERT INTO data_backups
            (backup_id, created_at, manifest_hash, source_references)
            VALUES (:id, :created, :hash, CAST(:sources AS jsonb))"""),
        {
            "id": document["backup_id"],
            "created": document["created_at"],
            "hash": hashlib.sha256(content).hexdigest(),
            "sources": json.dumps(document["sources"]),
        },
    )
