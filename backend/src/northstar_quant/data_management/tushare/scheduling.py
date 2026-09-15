"""Choose one retired contract, then a ready internal request belonging to it."""

from typing import Any

from sqlalchemy import Connection, text

from ..contract_data.lifecycle import SEARCH_START, completed
from .catalog import BY_KEY


def choose(connection: Connection, *, download_ready: bool) -> Any:
    connection.execute(text("SET LOCAL jit=off"))
    earliest = connection.scalar(
        text("""SELECT min(d.details->>'delist_date')
        FROM data_sync_contracts d LEFT JOIN data_contract_collections w ON w.scope=d.ts_code
        WHERE d.kind='1' AND d.planned_revision<>(SELECT revision FROM data_sync_settings)
        AND (w.scope IS NULL OR w.status IN ('COLLECTING','VERIFYING'))
        AND d.details->>'delist_date' ~ '^[0-9]{8}$'
        AND d.details->>'delist_date'>=:floor"""),
        dict(floor=SEARCH_START.strftime("%Y%m%d")),
    )
    eligible = []
    for contract in connection.execute(
        text("""SELECT d.* FROM data_sync_contracts d
        JOIN data_contract_collections w ON w.scope=d.ts_code
        WHERE w.status IN ('COLLECTING','VERIFYING')
        AND d.planning_error IS NULL""")
    ).mappings():
        try:
            life = completed(contract)
        except ValueError:
            continue
        if life.end >= SEARCH_START:
            if earliest is None or life.end.strftime("%Y%m%d") <= earliest:
                eligible.append(contract["ts_code"])
    parameters = {
        "eligible": eligible,
        "datasets": list(BY_KEY),
        "apis": [d.api for d in BY_KEY.values()],
        "download_ready": download_ready,
    }
    # Catalog refresh discovers newly retired contracts and never represents a
    # collection/publication itself. All price/product requests require an owner.
    return (
        connection.execute(
            text("""
        WITH last_lane AS MATERIALIZED (
            SELECT EXISTS(SELECT 1 FROM data_series_requests sr WHERE sr.request_id=a.request_id)
                AS research_series FROM data_sync_attempts a
            ORDER BY a.started_at DESC,a.generation DESC LIMIT 1
        ), ready AS MATERIALIZED (
            SELECT j.*,COALESCE(min(w.end_date),min(NULLIF(j.end_at,''))::date) AS owner_end,
                COALESCE(min(w.scope),min(sr.scope)) AS owner_scope,
                bool_or(sr.scope IS NOT NULL) AS research_series
            FROM data_sync_jobs j
            JOIN unnest(CAST(:datasets AS text[]),CAST(:apis AS text[])) d(dataset,api)
                ON j.dataset=d.dataset
            LEFT JOIN data_series_requests sr ON sr.request_id=j.request_id
            LEFT JOIN data_contract_requests cr ON cr.request_id=j.request_id
            LEFT JOIN data_contract_collections w ON w.scope=cr.scope
                AND w.status IN ('COLLECTING','VERIFYING')
                AND w.scope=ANY(CAST(:eligible AS text[]))
            WHERE j.status IN ('PENDING','WAITING') AND j.next_at<=now()
            AND (j.dataset='contracts' OR w.scope IS NOT NULL OR sr.scope IS NOT NULL)
            AND (j.source_generation IS NOT NULL OR (:download_ready AND COALESCE(
                (SELECT (api_next_at->>d.api)::timestamptz FROM data_sync_settings),
                '-infinity'::timestamptz)<=now()))
            AND (j.source_generation IS NOT NULL OR NOT EXISTS (
                SELECT 1 FROM data_sync_jobs b WHERE b.dataset=j.dataset AND b.status='BLOCKED'
                AND b.error LIKE 'Tushare 权限不足%'))
            GROUP BY j.request_id
        ), selected AS (
            SELECT r.request_id FROM ready r
            ORDER BY (r.dataset='contracts') DESC,
                COALESCE(r.research_series=(SELECT research_series FROM last_lane),false),
                (r.dataset='calendar') DESC,r.owner_end,r.owner_scope,
                (r.source_generation IS NOT NULL) DESC,
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
