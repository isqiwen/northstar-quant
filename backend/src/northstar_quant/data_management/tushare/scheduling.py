"""Choose one retired contract, then a ready internal request belonging to it."""

from typing import Any

from sqlalchemy import Connection, text

from .catalog import BY_KEY


def choose(connection: Connection, *, download_ready: bool) -> Any:
    connection.execute(text("SET LOCAL jit=off"))
    parameters = {
        "datasets": list(BY_KEY),
        "apis": [d.api for d in BY_KEY.values()],
        "download_ready": download_ready,
    }
    # Catalog refresh discovers newly retired contracts and never represents a
    # collection/publication itself. All price/product requests require an owner.
    return (
        connection.execute(
            text("""
        WITH ready AS MATERIALIZED (
            SELECT j.*,min(w.end_date) AS owner_end,min(w.scope) AS owner_scope
            FROM data_sync_jobs j
            JOIN unnest(CAST(:datasets AS text[]),CAST(:apis AS text[])) d(dataset,api)
                ON j.dataset=d.dataset
            LEFT JOIN data_contract_requests cr ON cr.request_id=j.request_id
            LEFT JOIN data_contract_collections w ON w.scope=cr.scope
                AND w.status IN ('COLLECTING','VERIFYING')
            WHERE j.status IN ('PENDING','WAITING') AND j.next_at<=now()
            AND (j.dataset IN ('contracts','calendar') OR w.scope IS NOT NULL)
            AND (j.source_generation IS NOT NULL OR (:download_ready AND COALESCE(
                (SELECT (api_next_at->>d.api)::timestamptz FROM data_sync_settings),
                '-infinity'::timestamptz)<=now()))
            AND (j.source_generation IS NOT NULL OR NOT EXISTS (
                SELECT 1 FROM data_sync_jobs b WHERE b.dataset=j.dataset AND b.status='BLOCKED'
                AND b.error LIKE 'Tushare 权限不足%'))
            GROUP BY j.request_id
        ), selected AS (
            SELECT r.request_id FROM ready r
            ORDER BY (r.dataset='contracts') DESC, (r.source_generation IS NOT NULL) DESC,
                r.owner_end DESC, r.owner_scope,
                (r.dataset='calendar') DESC,
                (SELECT max(a.started_at) FROM data_sync_attempts a
                    JOIN data_sync_jobs s USING(request_id)
                    WHERE s.dataset=r.dataset AND s.scope=r.scope) ASC NULLS FIRST,
                r.start_at,r.created_at,r.request_id LIMIT 1
        ) SELECT j.* FROM data_sync_jobs j JOIN selected USING(request_id)
        FOR UPDATE OF j SKIP LOCKED
    """),
            parameters,
        )
        .mappings()
        .one_or_none()
    )


def history_end(connection: Connection) -> Any:
    return connection.scalar(text("SELECT max(end_date) FROM data_contract_collections"))


def progress(connection: Connection) -> tuple[str | None, list[dict[str, Any]]]:
    boundary = history_end(connection)
    if boundary is None:
        return None, []
    row = (
        connection.execute(
            text("""SELECT count(*) AS total,
        count(*) FILTER(WHERE status='PUBLISHED') AS validated,
        count(*) FILTER(WHERE status='VERIFYING') AS waiting,
        count(*) FILTER(WHERE status='REJECTED') AS blocked,
        count(*) FILTER(WHERE status='COLLECTING') AS running,
        min(start_date) AS start, max(end_date) AS end,
        min(start_date) FILTER(WHERE status<>'PUBLISHED') AS oldest_pending
        FROM data_contract_collections""")
        )
        .mappings()
        .one()
    )
    from .store import serial

    return boundary.isoformat(), [{"lane": "contracts", **serial(row)}]
