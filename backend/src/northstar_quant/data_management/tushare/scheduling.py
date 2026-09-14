"""Initial history and subsequent updates share durable jobs and retry eligibility."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from .planning import HISTORY_START, target_day


def history_end(connection: Connection) -> date | None:
    # The first durable job fixes the initial batch boundary across toggles/restarts.
    first: datetime | None = connection.scalar(text("SELECT min(created_at) FROM data_sync_jobs"))
    if first is None:
        return None
    local = first.astimezone(ZoneInfo("Asia/Shanghai"))
    return local.date() if local.hour >= 18 else local.date() - timedelta(days=1)


def choose(connection: Connection, *, download_ready: bool) -> Any:
    boundary = history_end(connection)
    return (
        connection.execute(
            text("""
        WITH served AS (
            SELECT j.dataset,max(a.started_at) AS last_at
            FROM data_sync_attempts a JOIN data_sync_jobs j USING(request_id)
            GROUP BY j.dataset
        )
        SELECT j.* FROM data_sync_jobs j LEFT JOIN served s USING(dataset)
        WHERE j.status IN ('PENDING','WAITING') AND j.next_at<=now()
        AND (j.source_generation IS NOT NULL OR :download_ready)
        AND (j.source_generation IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM data_sync_jobs b WHERE b.dataset=j.dataset
            AND b.status='BLOCKED' AND b.error LIKE 'Tushare 权限不足%'))
        ORDER BY (j.source_generation IS NOT NULL) DESC,
            CASE j.dataset WHEN 'contracts' THEN 0 WHEN 'calendar' THEN 1 ELSE 2 END,
            (j.end_at > :boundary) DESC,
            CASE WHEN j.end_at > :boundary THEN left(j.start_at,7) END DESC,
            CASE WHEN j.end_at <= :boundary THEN left(j.start_at,7) END ASC,
            s.last_at ASC NULLS FIRST,
            CASE WHEN j.end_at > :boundary THEN j.start_at END DESC,
            j.start_at ASC,j.created_at,j.request_id
        LIMIT 1 FOR UPDATE OF j SKIP LOCKED
        """),
            {
                "boundary": boundary.isoformat() if boundary else target_day().isoformat(),
                "download_ready": download_ready,
            },
        )
        .mappings()
        .one_or_none()
    )


def progress(connection: Connection) -> tuple[str | None, list[dict[str, Any]]]:
    boundary = history_end(connection)
    if boundary is None:
        return None, []
    rows = connection.execute(
        text("""
        SELECT CASE WHEN end_at>:boundary THEN 'daily' ELSE 'history' END AS lane,
            count(*) AS total,
            count(*) FILTER (WHERE status='VALIDATED') AS validated,
            count(*) FILTER (WHERE status='WAITING') AS waiting,
            count(*) FILTER (WHERE status='BLOCKED') AS blocked,
            count(*) FILTER (WHERE status='RUNNING') AS running,
            min(start_at) FILTER (WHERE status<>'VALIDATED') AS oldest_pending
        FROM data_sync_jobs WHERE dataset NOT IN ('contracts','calendar') AND status<>'SPLIT'
        GROUP BY lane
    """),
        {"boundary": boundary.isoformat()},
    ).mappings()
    found = {row["lane"]: dict(row) for row in rows}
    result = []
    for lane in ("history", "daily"):
        row = found.get(
            lane,
            dict(
                lane=lane,
                total=0,
                validated=0,
                waiting=0,
                blocked=0,
                running=0,
                oldest_pending=None,
            ),
        )
        row["start"] = (
            HISTORY_START if lane == "history" else boundary + timedelta(days=1)
        ).isoformat()
        row["end"] = (boundary if lane == "history" else target_day()).isoformat()
        result.append(row)
    return boundary.isoformat(), result
