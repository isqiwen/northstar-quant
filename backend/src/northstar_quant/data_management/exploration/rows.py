"""Exact, pinned range reads with bounded Parquet batches and explicit conflicts."""

import hashlib
import io
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from threading import BoundedSemaphore
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from ..tushare import publication
from ..tushare.catalog import BY_KEY
from ..tushare.store import serial
from .catalog import pinned

_READERS = BoundedSemaphore(2)
_FIELDS = {
    "ts_code": ("合约代码", "供应商合约标识"),
    "trade_time": ("供应商时间", "Asia/Shanghai；未推断所属交易日或首次可得时间"),
    "trade_date": ("供应商日期", "供应商标签；周/月线需同时查看 end_date"),
    "end_date": ("计算截止日期", "供应商日期"),
    "open": ("开盘价", "供应商报价单位"),
    "high": ("最高价", "供应商报价单位"),
    "low": ("最低价", "供应商报价单位"),
    "close": ("收盘价", "供应商报价单位"),
    "vol": ("成交量", "供应商口径，尚未统一单双边语义"),
    "oi": ("持仓量", "供应商口径，尚未统一单双边语义"),
    "amount": ("原始成交额", "供应商接口单位"),
    "amount_cny": ("标准成交额", "元（CNY）"),
}


def read(
    engine: Engine,
    dataset: str,
    scope: str,
    start: str,
    end: str,
    receipt_ids: list[UUID],
    offset: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    if not _READERS.acquire(blocking=False):
        raise ValueError("已有两个数据查询运行，请稍后重试")
    try:
        return _read(engine, dataset, scope, start, end, receipt_ids, offset, limit)
    finally:
        _READERS.release()


def _read(
    engine: Engine,
    dataset: str,
    scope: str,
    start: str,
    end: str,
    receipt_ids: list[UUID],
    offset: int,
    limit: int,
) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    versions = pinned(engine, dataset, scope, start, end, receipt_ids)
    if sum(r["parquet_bytes"] for r in versions) > 32 * 1024 * 1024:
        raise ValueError("所选分片超过 32 MiB，请缩小范围")
    with engine.connect() as c:
        sources = [
            serial(r)
            for r in c.execute(
                text("""SELECT min(source_id::text) AS source_id,content_hash,
            bool_and(allow_download) AS allow_download
            FROM data_sources WHERE content_hash=ANY(CAST(:hashes AS text[])) 
            GROUP BY content_hash ORDER BY content_hash"""),
                {"hashes": [r["source_hash"] for r in versions]},
            ).mappings()
        ]
    export_allowed = (
        bool(versions)
        and all(any(s["content_hash"] == r["source_hash"] for s in sources) for r in versions)
        and all(s["allow_download"] for s in sources)
    )
    files = publication.storage()
    selected: dict[tuple[str, ...], dict[str, Any]] = {}
    origins: dict[tuple[str, ...], list[str]] = {}
    identity = BY_KEY[dataset].identity
    for version in versions:
        manifest = json.loads(files.read(version["manifest_hash"], version["manifest_bytes"]))
        if (
            manifest["dataset"] != dataset
            or manifest["scope"] != scope
            or manifest["quality"] != version["quality"]
            or manifest["parquet"]
            != {"content_hash": version["parquet_hash"], "byte_count": version["parquet_bytes"]}
        ):
            raise ValueError("发布清单与固定版本不一致")
        raw = files.read(version["parquet_hash"], version["parquet_bytes"])
        parquet = pq.ParquetFile(io.BytesIO(raw))
        if parquet.metadata.num_rows != version["row_count"] or parquet.metadata.num_rows > 10000:
            raise ValueError("Parquet 行数与固定版本不一致或超过单分片上限")
        if len(parquet.schema.names) > 64:
            raise ValueError("发布字段超过浏览上限")
        for batch in parquet.iter_batches(batch_size=256):
            for row in batch.to_pylist():
                if row.get("ts_code") != scope:
                    raise ValueError("发布记录不属于所选合约")
                clock = (
                    row.get("end_date")
                    if dataset in ("week", "month")
                    else row.get("trade_time", row.get("trade_date"))
                )
                day = (
                    datetime.strptime(clock[:10], "%Y-%m-%d").date()
                    if "-" in clock
                    else datetime.strptime(clock, "%Y%m%d").date()
                )
                if not start <= day.isoformat() <= end:
                    continue
                if len(json.dumps(row)) > 8192:
                    raise ValueError("单条记录超过浏览上限")
                key = tuple(str(row[field]) for field in identity)
                if key in selected and selected[key] != row:
                    raise ValueError(
                        "所选发布版本在同一记录身份上存在冲突，请到版本页核查；未自动覆盖"
                    )
                selected[key] = row
                origins.setdefault(key, []).append(str(version["receipt_id"]))
                if len(selected) > 20000:
                    raise ValueError("所选范围超过 20000 行，请缩小日期范围")
    ordered = sorted(selected)
    fields = []
    for name in sorted({f for row in selected.values() for f in row}):
        values = [r.get(name) for r in selected.values()]
        present = [v for v in values if v is not None and v != ""]
        low = high = None
        try:
            numbers = [Decimal(v) for v in present]
            if numbers and all(v.is_finite() for v in numbers):
                low, high = str(min(numbers)), str(max(numbers))
        except InvalidOperation:
            pass
        label, unit = _FIELDS.get(name, (name, "供应商原字段，单位未声明"))
        fields.append(
            {
                "key": name,
                "label": label,
                "unit": unit,
                "type": "精确文本",
                "missing": len(values) - len(present),
                "minimum": low,
                "maximum": high,
            }
        )
    ids = [str(r["receipt_id"]) for r in versions]
    binding = {"dataset": dataset, "scope": scope, "start": start, "end": end, "receipt_ids": ids}
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    page = [
        {**selected[k], "_key": json.dumps(k), "_receipts": origins[k]}
        for k in ordered[offset : offset + limit]
    ]
    return {
        **binding,
        "view_id": fingerprint,
        "rows": page,
        "total": len(ordered),
        "offset": offset,
        "limit": limit,
        "fields": fields,
        "sources": sources,
        "export_allowed": export_allowed,
        "versions": [serial(r) for r in versions],
        "note": (
            "供应商修订后历史，非首次可得行情。图表仅显示当前页原始记录；"
            "字段统计针对当前固定查询范围。"
        ),
    }
