"""Discover published nonempty responses and open a verified populated date range."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from ..contract_data.catalog import RECEIPTS, require_receipt
from ..tushare.store import serial
from . import rows
from .catalog import BROWSABLE_DATASETS
from .instruments import display_name, localized_products
from .parquet import day_label


def available(
    engine: Engine, dataset: str, exchange: str, product: str, search: str, offset: int
) -> dict[str, Any]:
    if dataset and dataset not in BROWSABLE_DATASETS:
        raise ValueError("请选择已支持的数据类型")
    with engine.connect() as c:
        found = (
            c.execute(
                text(
                    RECEIPTS
                    + """, published AS (
                SELECT a.publication_id,r.receipt_id,r.row_count,j.dataset,j.scope,
                j.start_at,j.end_at,
                c.exchange,c.product,c.details->>'name' AS name
                FROM admitted a JOIN data_sync_receipts r ON r.receipt_id=a.receipt_id
                JOIN data_sync_jobs j ON j.request_id=r.request_id
                LEFT JOIN data_sync_contracts c ON c.ts_code=j.scope
                WHERE r.row_count>0 AND j.scope=a.contract_scope
                AND j.dataset=ANY(:datasets)
                AND (:exchange='' OR c.exchange=:exchange)
                AND (:product='' OR c.product=:product)
                AND (:search='' OR position(lower(:search) in lower(j.scope))>0
                    OR position(lower(:search) in lower(c.details->>'name'))>0
                    OR (c.exchange='CFFEX' AND c.product=ANY(:localized)))
                ), grouped AS (
                SELECT scope,min(start_at) AS available_start,max(end_at) AS available_end,
                array_agg(DISTINCT dataset ORDER BY dataset) AS periods,
                count(*) AS publications FROM published GROUP BY scope
                ), preferred AS (
                SELECT DISTINCT ON (scope) * FROM published
                ORDER BY scope,end_at DESC,
                CASE dataset WHEN 'daily' THEN 0 WHEN '15min' THEN 1 ELSE 2 END,dataset,receipt_id
                ) SELECT p.*,g.available_start,g.available_end,g.periods,g.publications,
                count(*) OVER() AS total FROM preferred p JOIN grouped g USING(scope)
                ORDER BY available_end DESC,scope LIMIT 10 OFFSET :offset"""
                ),
                dict(
                    datasets=[dataset] if dataset else list(BROWSABLE_DATASETS),
                    exchange=exchange,
                    product=product,
                    search=search,
                    localized=localized_products(search),
                    offset=offset,
                ),
            )
            .mappings()
            .all()
        )
    return {
        "rows": [
            {
                **serial(r),
                "display_name": display_name(r["scope"], r["name"], r["exchange"], r["product"]),
            }
            for r in found
        ],
        "total": found[0]["total"] if found else 0,
    }


def open_published(engine: Engine, receipt_id: UUID) -> dict[str, Any]:
    with engine.connect() as c:
        require_receipt(c, receipt_id)
        version = (
            c.execute(
                text("""SELECT r.*,j.dataset,j.scope,j.start_at,j.end_at
            FROM data_sync_receipts r JOIN data_sync_jobs j USING(request_id)
            WHERE r.receipt_id=:id"""),
                {"id": receipt_id},
            )
            .mappings()
            .one_or_none()
        )
    if version is None or version["dataset"] not in BROWSABLE_DATASETS:
        raise ValueError("该发布版本不存在或不支持浏览")
    # Read just the selected publication, not all historical files for a contract.
    scan = rows.verified_scan(dict(version), version["start_at"], version["end_at"])
    days = []
    for record in scan.rows():
        day = day_label(record[scan.clock])
        if scan.period:
            day = min(day, day_label(record["end_date"]))
        days.append(day)
    if not days:
        raise ValueError("该发布版本没有可浏览记录，请刷新已有数据列表")
    end = max(days)
    start = max(min(days), (date.fromisoformat(end) - timedelta(days=30)).isoformat())
    return rows.read(engine, version["dataset"], version["scope"], start, end, [receipt_id])


def open_instrument(engine: Engine, scope: str, dataset: str) -> dict[str, Any]:
    """Choose a current nonempty native publication, then pin its actual data range."""
    if dataset and dataset not in BROWSABLE_DATASETS:
        raise ValueError("请选择已支持的数据类型")
    with engine.connect() as c:
        receipt = c.execute(
            text(
                RECEIPTS
                + """SELECT r.receipt_id
            FROM admitted a JOIN data_sync_receipts r ON r.receipt_id=a.receipt_id
            JOIN data_sync_jobs j ON j.request_id=r.request_id
            WHERE j.scope=:scope AND a.contract_scope=:scope AND j.dataset=ANY(:datasets)
            AND r.row_count>0
            ORDER BY j.end_at DESC,
            CASE j.dataset WHEN 'daily' THEN 0 WHEN '15min' THEN 1 ELSE 2 END,
            j.dataset,r.receipt_id LIMIT 1"""
            ),
            {"scope": scope, "datasets": [dataset] if dataset else list(BROWSABLE_DATASETS)},
        ).scalar_one_or_none()
    if receipt is None:
        raise ValueError("该合约周期尚无已发布数据，请选择其他周期")
    return open_published(engine, receipt)
