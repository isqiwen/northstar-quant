"""Discover published nonempty responses and open a verified populated date range."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from ..tushare.store import serial
from . import rows
from .catalog import BROWSABLE_DATASETS
from .parquet import day_label


def available(
    engine: Engine, dataset: str, exchange: str, product: str, search: str, offset: int
) -> dict[str, Any]:
    if dataset and dataset not in BROWSABLE_DATASETS:
        raise ValueError("请选择已支持的数据类型")
    with engine.connect() as c:
        found = (
            c.execute(
                text("""SELECT r.receipt_id,r.row_count,j.dataset,j.scope,j.start_at,j.end_at,
                c.exchange,c.product,count(*) OVER() AS total
                FROM data_sync_jobs j JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
                LEFT JOIN data_sync_contracts c ON c.ts_code=j.scope
                WHERE j.status<>'SPLIT' AND r.row_count>0
                AND j.dataset=ANY(:datasets)
                AND (:exchange='' OR c.exchange=:exchange)
                AND (:product='' OR c.product=:product)
                AND (:search='' OR position(lower(:search) in lower(j.scope))>0)
                ORDER BY j.end_at DESC,j.scope,j.dataset,r.receipt_id
                LIMIT 10 OFFSET :offset"""),
                dict(
                    datasets=[dataset] if dataset else list(BROWSABLE_DATASETS),
                    exchange=exchange,
                    product=product,
                    search=search,
                    offset=offset,
                ),
            )
            .mappings()
            .all()
        )
    return {"rows": [serial(r) for r in found], "total": found[0]["total"] if found else 0}


def open_published(engine: Engine, receipt_id: UUID) -> dict[str, Any]:
    with engine.connect() as c:
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
