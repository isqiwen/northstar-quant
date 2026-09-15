"""Short quota admission and connection-owned task liveness, shared by all processes."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, text

from northstar_quant import code_revision

from ..maintenance import library_write
from . import batching, planning, scheduling
from .catalog import BY_KEY
from .store import serial

_DISPATCH = 0x4E53515453594E
_CATALOG = 0x4E5351434154


def lock_catalog(connection: Connection) -> None:
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CATALOG})


def prepare(engine: Engine) -> None:
    """Keep planning/catalog writes ordered without serializing data processing."""
    with library_write(engine), engine.begin() as connection:
        if connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _CATALOG}):
            planning.refresh(engine)
            planning.plan(engine)
            from ..series_data.planning import plan as plan_series

            plan_series(engine)


def _own(connection: Connection, request_id: Any) -> bool:
    key = int.from_bytes(sha256(f"tushare-job:{request_id}".encode()).digest()[:8], signed=True)
    return bool(connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}))


def _recover(connection: Connection) -> None:
    # A live slow download keeps its lock. Timeouts/lease expiry never steal it.
    running = (
        connection.execute(
            text("SELECT request_id,generation FROM data_sync_jobs WHERE status='RUNNING'")
        )
        .mappings()
        .all()
    )
    for row in running:
        if not _own(connection, row["request_id"]):
            continue
        connection.execute(
            text("""UPDATE data_sync_jobs SET status='PENDING',generation=NULL,
            source_generation=COALESCE(source_generation,
                (SELECT generation FROM data_sync_attempts WHERE generation=:g
                 AND source_hash IS NOT NULL)),
            error='进程中断，继续未提交分片',next_at=now()
            WHERE request_id=:id AND generation=:g AND status='RUNNING'"""),
            {"id": row["request_id"], "g": row["generation"]},
        )
        connection.execute(
            text("""UPDATE data_sync_attempts SET finished_at=now(),outcome='INTERRUPTED'
            WHERE generation=:g AND finished_at IS NULL"""),
            {"g": row["generation"]},
        )


def claim(engine: Engine, owner: Connection) -> dict[str, Any] | None:
    """Reserve one persisted quota slot and keep its job lock until the caller exits."""
    with engine.begin() as recovery:
        if recovery.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _DISPATCH}):
            _recover(recovery)
    with engine.begin() as connection:
        if not connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _DISPATCH}
        ):
            return None
        config = connection.execute(text("SELECT * FROM data_sync_settings")).mappings().one()
        if not config["enabled"]:
            return None
        row = scheduling.choose(
            connection, download_ready=config["next_request_at"] <= datetime.now(UTC)
        )
        if row is None:
            return None
        selected = serial(batching.combine(connection, row))
        if not _own(owner, selected["request_id"]):
            return None
        generation = uuid4()
        connection.execute(
            text("""UPDATE data_sync_jobs SET status='RUNNING',generation=:g,
            attempts=attempts+CASE WHEN source_generation IS NULL THEN 1 ELSE 0 END,
            updated_at=now() WHERE request_id=:id"""),
            {"g": generation, "id": selected["request_id"]},
        )
        connection.execute(
            text("""INSERT INTO data_sync_attempts
            (generation,request_id,parent_generation,code_revision)
            VALUES(:g,:id,:parent,:revision)"""),
            {
                "g": generation,
                "id": selected["request_id"],
                "parent": selected["source_generation"],
                "revision": code_revision(),
            },
        )
        if not selected["source_generation"]:
            dataset = BY_KEY[selected["dataset"]]
            connection.execute(
                text("""UPDATE data_sync_settings SET next_request_at=now()+:delay,
                api_next_at=api_next_at || jsonb_build_object(
                    CAST(:api AS text),now()+:api_delay)"""),
                {
                    "delay": timedelta(seconds=60 / config["requests_per_minute"]),
                    "api": dataset.api,
                    "api_delay": timedelta(seconds=60 / dataset.requests_per_minute),
                },
            )
        selected["generation"] = generation
        selected["attempts"] += int(not selected["source_generation"])
        return selected
