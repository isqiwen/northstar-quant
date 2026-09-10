"""Immutable Parquet response snapshots; a manifest is the only read entry."""

import io
import json
from typing import Any

from ..files import SourceFiles
from ..publications import PublishedDatasets


def storage() -> SourceFiles:
    market = PublishedDatasets.from_environment()
    return SourceFiles(market.root / "tushare", max_total_bytes=2**60, shared_read=True)


def publish(
    rows: list[dict[str, Any]], quality: dict[str, Any], job: dict[str, Any], archive: SourceFiles
) -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    # Strings preserve exact supplier decimal values and heterogeneous metadata.
    # The manifest declares normalized amount units and preserves supplier fields.
    columns = sorted({key for row in rows for key in row})
    table = pa.table(
        {
            key: pa.array(
                [None if row.get(key) is None else str(row[key]) for row in rows], type=pa.string()
            )
            for key in columns
        }
    )
    stream = io.BytesIO()
    pq.write_table(table, stream, compression="zstd")
    files = storage()
    parquet = archive.store(stream.getvalue())
    files.store(stream.getvalue())
    manifest = {
        "dataset": job["dataset"],
        "scope": job["scope"],
        "parameters": job["parameters"],
        "start_at": job["start_at"],
        "end_at": job["end_at"],
        "quality": quality,
        "timezone": "Asia/Shanghai",
        "time_basis": "SUPPLIER_HISTORICAL_LABEL",
        "amount_cny_unit": "CNY",
        "parquet": parquet.to_dict(),
    }
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    artifact = archive.store(content)
    files.store(content)
    return {
        **artifact.to_dict(),
        "parquet_hash": parquet.content_hash,
        "parquet_bytes": parquet.byte_count,
    }


def read_snapshot(manifest_hash: str, byte_count: int, *, limit: int = 100) -> dict[str, Any]:
    import pyarrow.parquet as pq

    if not 1 <= limit <= 1000:
        raise ValueError("查询行数必须在 1–1000 之间")
    files = storage()
    manifest = json.loads(files.read(manifest_hash, byte_count))
    parquet = manifest["parquet"]
    raw = files.read(parquet["content_hash"], parquet["byte_count"])
    table = pq.read_table(io.BytesIO(raw))
    return {**manifest, "row_count": table.num_rows, "rows": table.slice(0, limit).to_pylist()}
