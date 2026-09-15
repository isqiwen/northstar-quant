"""Research series discovery and exact published interval selection."""

from typing import Any

from sqlalchemy import Engine, text

from ..tushare.store import serial
from .planning import DATASETS


def query(
    engine: Engine, *, dataset: str = "", search: str = "", offset: int = 0
) -> dict[str, Any]:
    if dataset and dataset not in DATASETS:
        raise ValueError("未知研究序列类型")
    if offset < 0:
        raise ValueError("页码无效")
    with engine.connect() as c:
        rows = (
            c.execute(
                text("""WITH published AS (
            SELECT DISTINCT ON(dataset,scope) dataset,scope,publication_id AS snapshot_id,
                start_date,end_date,created_at,manifest->>'name' AS name
            FROM data_series_publications
            ORDER BY dataset,scope,end_date DESC,created_at DESC,publication_id
        ), progress AS (
            SELECT s.dataset,s.scope,count(*) FILTER(WHERE j.status='VALIDATED') AS received,
                count(*) FILTER(WHERE j.status IN ('PENDING','RUNNING')) AS collecting,
                count(*) FILTER(WHERE j.status='WAITING') AS waiting,
                count(*) FILTER(WHERE j.status='BLOCKED'
                    OR s.publication_error IS NOT NULL) AS blocked,
                max(s.publication_error) AS error
            FROM data_series_requests s JOIN data_sync_jobs j USING(request_id)
            WHERE j.dataset=s.dataset GROUP BY s.dataset,s.scope
        ), items AS (
            SELECT s.dataset,s.scope,s.name,s.exchange,s.product,s.planned_through,
                p.snapshot_id,p.start_date,p.end_date,p.created_at,
                COALESCE(g.received,0) AS received,COALESCE(g.collecting,0) AS collecting,
                COALESCE(g.waiting,0) AS waiting,COALESCE(g.blocked,0) AS blocked,g.error
            FROM data_series_collections s LEFT JOIN published p USING(dataset,scope)
            LEFT JOIN progress g USING(dataset,scope) WHERE s.dataset<>'index'
            UNION ALL
            SELECT p.dataset,p.scope,p.name,'','',s.planned_through,
                p.snapshot_id,p.start_date,p.end_date,p.created_at,
                COALESCE(g.received,0),COALESCE(g.collecting,0),COALESCE(g.waiting,0),COALESCE(g.blocked,0),g.error
            FROM published p JOIN data_series_collections s ON s.dataset='index' AND s.scope='ALL'
            LEFT JOIN progress g ON g.dataset='index' AND g.scope='ALL' WHERE p.dataset='index'
            UNION ALL
            SELECT s.dataset,s.scope,s.name,'','',s.planned_through,NULL,NULL,NULL,NULL,
                COALESCE(g.received,0),COALESCE(g.collecting,0),COALESCE(g.waiting,0),COALESCE(g.blocked,0),g.error
            FROM data_series_collections s LEFT JOIN progress g USING(dataset,scope)
            WHERE s.dataset='index' AND NOT EXISTS(SELECT 1 FROM published WHERE dataset='index')
        ) SELECT *,count(*) OVER() AS total FROM items
        WHERE (:dataset='' OR dataset=:dataset)
        AND (:search='' OR position(lower(:search) in lower(scope||' '||name))>0)
        ORDER BY dataset,scope LIMIT 20 OFFSET :offset"""),
                dict(dataset=dataset, search=search, offset=offset),
            )
            .mappings()
            .all()
        )
    return dict(rows=[serial(r) for r in rows], total=rows[0]["total"] if rows else 0)


def versions(engine: Engine, *, dataset: str, scope: str, offset: int = 0) -> dict[str, Any]:
    if dataset not in DATASETS or offset < 0:
        raise ValueError("序列查询无效")
    with engine.connect() as c:
        rows = (
            c.execute(
                text("""SELECT publication_id AS snapshot_id,scope,dataset,
            start_date,end_date,created_at,count(*) OVER() AS total
            FROM data_series_publications WHERE dataset=:dataset AND scope=:scope
            ORDER BY end_date DESC,created_at DESC,publication_id LIMIT 20 OFFSET :offset"""),
                dict(dataset=dataset, scope=scope, offset=offset),
            )
            .mappings()
            .all()
        )
    return dict(rows=[serial(r) for r in rows], total=rows[0]["total"] if rows else 0)


def retry(engine: Engine, *, dataset: str, scope: str) -> dict[str, Any]:
    if dataset not in DATASETS:
        raise ValueError("未知研究序列类型")
    with engine.begin() as c:
        result = c.execute(
            text("""UPDATE data_series_requests SET processed_receipt_id=NULL,
            publication_error=NULL WHERE dataset=:dataset AND scope=:scope
            AND publication_error IS NOT NULL"""),
            dict(dataset=dataset, scope="ALL" if dataset == "index" else scope),
        )
    return dict(retried=result.rowcount)
