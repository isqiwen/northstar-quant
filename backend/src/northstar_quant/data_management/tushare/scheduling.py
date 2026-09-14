"""Initial history and subsequent updates share durable jobs and retry eligibility."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from .origins import HISTORY_START
from .planning import target_day


def history_end(connection: Connection) -> date | None:
    # The first durable job fixes the initial batch boundary across toggles/restarts.
    first: datetime | None = connection.scalar(text("SELECT min(created_at) FROM data_sync_jobs"))
    if first is None:
        return None
    local = first.astimezone(ZoneInfo("Asia/Shanghai"))
    return local.date() if local.hour >= 18 else local.date() - timedelta(days=1)


def choose(connection: Connection, *, download_ready: bool) -> Any:
    boundary = history_end(connection)
    from .catalog import BY_KEY

    parameters = {
        "boundary": boundary.isoformat() if boundary else target_day().isoformat(),
        "floor": HISTORY_START.isoformat(),
        "datasets": list(BY_KEY),
        "apis": [item.api for item in BY_KEY.values()],
    }
    # Reprocessing always precedes downloads. Each indexed probe returns at most
    # one candidate per dataset/lane; only this bounded set participates in sorting.
    for retained in (True, False):
        if not retained and not download_ready:
            continue
        source = "IS NOT NULL" if retained else "IS NULL"
        probes = []
        for recent, direction in ((False, "ASC"), (True, "DESC")):
            relation = ">" if recent else "<="
            probes.append(f"""
                SELECT candidate.* FROM unnest(
                    CAST(:datasets AS text[]),CAST(:apis AS text[])) AS d(dataset,api)
                CROSS JOIN LATERAL (
                    SELECT request_id,dataset,start_at,end_at,created_at
                    FROM data_sync_jobs j
                    WHERE ({str(retained).lower()} OR COALESCE(
                        (SELECT (api_next_at->>d.api)::timestamptz FROM data_sync_settings),
                        '-infinity'::timestamptz)<=now())
                    AND j.dataset=d.dataset AND j.status IN ('PENDING','WAITING')
                    AND j.next_at<=now() AND j.source_generation {source}
                    AND (j.start_at='' OR j.start_at>=:floor)
                    AND j.end_at {relation} :boundary
                    AND NOT EXISTS (SELECT 1 FROM data_sync_jobs b
                        WHERE b.dataset=j.dataset AND b.status='BLOCKED'
                        AND b.error LIKE 'Tushare 权限不足%' AND {str(not retained).lower()})
                    ORDER BY j.start_at {direction},j.created_at,j.request_id LIMIT 1
                ) candidate
            """)
        query = " UNION ALL ".join(probes)
        row = (
            connection.execute(
                text(f"""
            WITH candidates AS ({query}), served AS (
                SELECT j.dataset,max(a.started_at) AS last_at
                FROM data_sync_attempts a JOIN data_sync_jobs j USING(request_id)
                GROUP BY j.dataset
            ), selected AS (
                SELECT c.request_id FROM candidates c LEFT JOIN served s USING(dataset)
                ORDER BY CASE c.dataset WHEN 'contracts' THEN 0 WHEN 'calendar' THEN 1 ELSE 2 END,
                    (c.end_at>:boundary) DESC,
                    CASE WHEN c.end_at>:boundary THEN left(c.start_at,7) END DESC,
                    CASE WHEN c.end_at<=:boundary THEN left(c.start_at,7) END ASC,
                    s.last_at ASC NULLS FIRST,
                    CASE WHEN c.end_at>:boundary THEN c.start_at END DESC,
                    c.start_at,c.created_at,c.request_id LIMIT 1
            )
            SELECT j.* FROM data_sync_jobs j JOIN selected USING(request_id)
            FOR UPDATE OF j SKIP LOCKED
        """),
                parameters,
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return row
    return None


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
