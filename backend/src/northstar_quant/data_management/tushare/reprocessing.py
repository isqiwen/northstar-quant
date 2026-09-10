"""Queue an observed retained response for the existing synchronization worker."""

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from ..maintenance import library_write
from .store import job


def enqueue(engine: Engine, *, request_id: UUID, source_generation: UUID) -> dict[str, Any]:
    with library_write(engine), engine.begin() as connection:
        current = (
            connection.execute(
                text("SELECT * FROM data_sync_jobs WHERE request_id=:id FOR UPDATE"),
                {"id": request_id},
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            raise LookupError("同步分片不存在")
        if current["status"] in ("RUNNING", "SPLIT"):
            raise ValueError("分片正在处理或已经拆分，请刷新记录")
        latest = connection.scalar(
            text("""SELECT generation FROM data_sync_attempts
            WHERE request_id=:id AND source_hash IS NOT NULL AND parent_generation IS NULL
            AND finished_at IS NOT NULL ORDER BY started_at DESC,generation DESC LIMIT 1"""),
            {"id": request_id},
        )
        if latest != source_generation:
            raise ValueError("只能重处理当前最新的已留存响应，请刷新记录")
        if current["source_generation"] not in (None, source_generation):
            raise ValueError("已有另一份原文等待处理，请刷新记录")
        connection.execute(
            text("""UPDATE data_sync_jobs SET source_generation=:source,status='PENDING',
            next_at=now(),error=NULL,updated_at=now() WHERE request_id=:id"""),
            {"source": source_generation, "id": request_id},
        )
    return job(engine, request_id)
