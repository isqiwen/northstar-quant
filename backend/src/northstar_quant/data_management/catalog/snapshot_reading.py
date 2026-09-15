"""Read exact standard values from a fixed snapshot, without supplier/ORM imports."""

import io
from decimal import Decimal
from pathlib import Path
from typing import Any

from .partitioned import checked
from .snapshots import load


def query(
    root: Path,
    snapshot_id: str,
    *,
    domain: str,
    contract: str = "",
    series: str = "",
    start: str = "",
    end: str = "",
    offset: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    if offset < 0 or not 1 <= limit <= 1000 or (start and end and start > end):
        raise ValueError("标准目录查询范围无效")
    from datetime import date

    for value in (start, end):
        if value:
            date.fromisoformat(value)
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    manifest = load(root, snapshot_id)
    if contract and (
        manifest["entity_type"] != "REAL_CONTRACT" or contract != manifest["scope"].split(".")[0]
    ):
        raise ValueError("合约不属于所选固定快照")
    if series and (manifest["entity_type"] == "REAL_CONTRACT" or series != manifest["scope"]):
        raise ValueError("序列不属于所选固定快照")
    if domain not in {entry["domain"] for entry in manifest["files"]}:
        raise ValueError("数据类型不属于所选固定快照")
    rows = []
    total = 0
    for entry in manifest["files"]:
        if entry["domain"] != domain:
            continue
        table = pq.ParquetFile(io.BytesIO(checked(root, entry)))
        if table.metadata.num_rows != entry["rows"]:
            raise ValueError("分区行数与清单不一致")
        for batch in table.iter_batches(batch_size=512):
            for row in batch.to_pylist():
                if contract and row.get("contract", contract) != contract:
                    continue
                if series and row.get("series") != series:
                    continue
                day = (
                    row.get("period_end")
                    or row.get("trading_day")
                    or row.get("timestamp_label")
                    or row.get("calendar_date")
                )
                if (start or end) and day is None:
                    raise ValueError("该领域记录没有日期维度")
                if day and ((start and str(day)[:10] < start) or (end and str(day)[:10] > end)):
                    continue
                if offset <= total < offset + limit:
                    rows.append(
                        {k: format(v, "f") if isinstance(v, Decimal) else v for k, v in row.items()}
                    )
                total += 1
    return dict(
        snapshot_id=snapshot_id, domain=domain, rows=rows, total=total, offset=offset, limit=limit
    )
