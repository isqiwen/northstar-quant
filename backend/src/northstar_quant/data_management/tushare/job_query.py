"""Filtered, bounded browsing of the complete durable synchronization queue."""

from typing import Any

from sqlalchemy import Engine, text

from .catalog import BY_KEY
from .store import serial

STATUSES = frozenset({"PENDING", "RUNNING", "WAITING", "BLOCKED", "VALIDATED", "SPLIT"})


def search(engine: Engine, *, dataset: str, status: str, offset: int, limit: int) -> dict[str, Any]:
    if dataset and dataset not in BY_KEY:
        raise ValueError("未知数据类型")
    if status and status not in STATUSES:
        raise ValueError("未知同步状态")
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("分页范围无效")
    conditions = []
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    for name, value in (("dataset", dataset), ("status", status)):
        if value:
            conditions.append(f"{name}=:{name}")
            params[name] = value
    where = " AND ".join(conditions) or "true"
    # Count and rows belong to one snapshot even while the worker changes states.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        total = c.scalar(text(f"SELECT count(*) FROM data_sync_jobs WHERE {where}"), params)
        rows = c.execute(
            text(f"""SELECT request_id,dataset,scope,start_at,end_at,status,
            attempts,next_at,error,receipt_id,updated_at FROM data_sync_jobs WHERE {where}
            ORDER BY CASE status WHEN 'BLOCKED' THEN 0 WHEN 'RUNNING' THEN 1
                WHEN 'WAITING' THEN 2 WHEN 'VALIDATED' THEN 3 ELSE 4 END,
                updated_at DESC,request_id
            LIMIT :limit OFFSET :offset"""),
            params,
        ).mappings()
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [serial(r) for r in rows],
        }
