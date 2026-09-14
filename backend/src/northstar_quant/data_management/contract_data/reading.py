"""Ordinary queries pin published package inputs, never latest staging receipts."""

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from ..exploration.rows import read
from .catalog import RECEIPTS, require_receipt


def query(engine: Engine, *, receipt_ids: list[UUID], **values: Any) -> dict[str, Any]:
    with engine.connect() as c:
        if not receipt_ids:
            receipt_ids = list(
                c.scalars(
                    text(
                        RECEIPTS
                        + """SELECT DISTINCT r.receipt_id
                FROM admitted a JOIN data_sync_receipts r ON r.receipt_id=a.receipt_id
                JOIN data_sync_jobs j ON j.request_id=r.request_id
                WHERE a.contract_scope=:scope AND j.scope=:scope AND j.dataset=:dataset
                AND j.start_at<=:end AND j.end_at>=:start LIMIT 33"""
                    ),
                    values,
                )
            )
            if not receipt_ids:
                raise ValueError("所选范围没有完整合约发布；请查看合约下载与验收状态")
        for identity in receipt_ids:
            require_receipt(c, identity)
    return read(engine, receipt_ids=receipt_ids, **values)
