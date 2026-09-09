"""Data-owned bounded discovery and version selection for the browsing workspace."""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from ..tushare.catalog import BY_KEY, EXCHANGES
from ..tushare.store import serial

PRICE_DATASETS = ("1min", "5min", "15min", "30min", "60min", "daily", "week", "month", "adjusted")


def interval(dataset: str, scope: str, start: str, end: str) -> tuple[date, date]:
    if dataset not in PRICE_DATASETS or not 1 <= len(scope) <= 40:
        raise ValueError("请选择已支持的行情周期和合约")
    left, right = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (right - left).days <= 365:
        raise ValueError("单次浏览范围为 1–366 个自然日")
    return left, right


def overview(engine: Engine) -> dict[str, Any]:
    with engine.connect() as c:
        counts = {
            r["dataset"]: dict(r)
            for r in c.execute(
                text("""
            SELECT j.dataset,count(*) FILTER (WHERE j.status<>'SPLIT') AS windows,
            count(*) FILTER (WHERE j.status='VALIDATED') AS validated,
            count(*) FILTER (WHERE j.status='BLOCKED') AS blocked,
            count(*) FILTER (WHERE j.status='WAITING') AS waiting,
            max(j.checked_at) AS checked_at FROM data_sync_jobs j GROUP BY dataset
        """)
            ).mappings()
        }
        products = [
            dict(r)
            for r in c.execute(
                text("""SELECT exchange,product,count(*) AS contracts
            FROM data_sync_contracts GROUP BY exchange,product ORDER BY exchange,product""")
            ).mappings()
        ]
    return {
        "datasets": [
            {**d.public(), **serial(counts.get(d.key, {})), "browsable": d.key in PRICE_DATASETS}
            for d in BY_KEY.values()
        ],
        "exchanges": list(EXCHANGES),
        "products": products,
    }


def contracts(
    engine: Engine, exchange: str, product: str, search: str, offset: int
) -> dict[str, Any]:
    with engine.connect() as c:
        rows = (
            c.execute(
                text("""SELECT ts_code,exchange,product,kind,details,planning_error,
            count(*) OVER() AS total FROM data_sync_contracts
            WHERE (:exchange='' OR exchange=:exchange) AND (:product='' OR product=:product)
            AND (:search='' OR position(lower(:search) in lower(ts_code))>0)
            ORDER BY ts_code LIMIT 50 OFFSET :offset"""),
                {"exchange": exchange, "product": product, "search": search, "offset": offset},
            )
            .mappings()
            .all()
        )
    return {"rows": [serial(r) for r in rows], "total": rows[0]["total"] if rows else 0}


def versions(
    engine: Engine, dataset: str, scope: str, start: str, end: str, offset: int = 0
) -> dict[str, Any]:
    interval(dataset, scope, start, end)
    with engine.connect() as c:
        rows = (
            c.execute(
                text("""SELECT r.*,j.dataset,j.scope,j.start_at,j.end_at,
            count(*) OVER() AS total FROM data_sync_receipts r
            JOIN data_sync_jobs j USING(request_id)
            WHERE j.dataset=:dataset AND j.scope=:scope AND j.start_at<=:end AND j.end_at>=:start
            ORDER BY r.created_at DESC,r.receipt_id LIMIT 50 OFFSET :offset"""),
                dict(dataset=dataset, scope=scope, start=start, end=end, offset=offset),
            )
            .mappings()
            .all()
        )
    return {"rows": [serial(r) for r in rows], "total": rows[0]["total"] if rows else 0}


def pinned(
    engine: Engine, dataset: str, scope: str, start: str, end: str, receipt_ids: list[UUID]
) -> list[dict[str, Any]]:
    interval(dataset, scope, start, end)
    with engine.connect() as c:
        rows = (
            c.execute(
                text("""SELECT r.*,j.dataset,j.scope,j.start_at,j.end_at
            FROM data_sync_receipts r JOIN data_sync_jobs j USING(request_id)
            WHERE j.dataset=:dataset AND j.scope=:scope AND j.start_at<=:end AND j.end_at>=:start
            AND ((:explicit AND r.receipt_id=ANY(CAST(:ids AS uuid[])))
                 OR (NOT :explicit AND r.receipt_id=j.receipt_id AND j.status<>'SPLIT'))
            ORDER BY j.start_at,r.receipt_id LIMIT 33"""),
                dict(
                    dataset=dataset,
                    scope=scope,
                    start=start,
                    end=end,
                    explicit=bool(receipt_ids),
                    ids=receipt_ids,
                ),
            )
            .mappings()
            .all()
        )
    if len(rows) > 32:
        raise ValueError("本次范围超过 32 个发布分片，请缩小日期范围")
    if receipt_ids and {r["receipt_id"] for r in rows} != set(receipt_ids):
        raise ValueError("固定版本不属于所选合约、周期或日期范围")
    return [dict(r) for r in rows]
