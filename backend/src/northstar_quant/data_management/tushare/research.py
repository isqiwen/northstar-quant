"""Prepare one fixed provider period through the existing durable research queue.

The caller binds a reviewed label interpretation and a materialized session. This
operation neither downloads data nor infers the supplier's historical first clock.
"""

import csv
import io
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from ..library import DataLibrary
from ..maintenance import library_write
from ..research_input import ImportSpec
from . import normalization

RULE = "tushare-research/1"
_SUFFIXES = {
    "SHFE": "SHF",
    "CZCE": "ZCE",
    "CFFEX": "CFX",
    "DCE": "DCE",
    "INE": "INE",
    "GFEX": "GFE",
}


def submit(
    library: DataLibrary,
    *,
    receipt_id: UUID,
    request_id: UUID,
    specification: ImportSpec,
    label_convention: str,
    interpretation_reference: str,
) -> dict[str, object]:
    """Pin an existing receipt; return PENDING before canonical processing begins."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if label_convention not in {"BAR_START", "BAR_END"}:
        raise ValueError("a reviewed BAR_START or BAR_END label interpretation is required")
    if (
        not interpretation_reference.strip()
        or len(interpretation_reference) > 512
        or any(ord(char) < 32 for char in interpretation_reference)
    ):
        raise ValueError("a bounded reference for the time interpretation is required")
    if specification.availability_basis != "FINAL_REVISED":
        raise ValueError("supplier historical bars require explicit FINAL_REVISED clock semantics")
    if specification.timezone != "Asia/Shanghai":
        raise ValueError("Tushare futures timestamps require Asia/Shanghai")
    with library_write(library._engine):
        with library._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""SELECT r.*,j.dataset,j.scope,j.parameters,c.exchange,c.product
                    FROM data_sync_receipts r
                    JOIN data_sync_jobs j ON j.request_id=r.request_id
                    JOIN data_sync_contracts c ON c.ts_code=j.scope WHERE r.receipt_id=:id"""),
                    {"id": receipt_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("fixed Tushare receipt not found")
            source_id = connection.scalar(
                text("""SELECT source_id FROM data_sources WHERE input_kind='TUSHARE_RESPONSE'
                    AND content_hash=:hash AND byte_count=:size ORDER BY received_at,source_id
                    LIMIT 1"""),
                {"hash": row["source_hash"], "size": row["source_bytes"]},
            )
        if source_id is None:
            raise ValueError("fixed Tushare receipt has no retained source evidence")
        frequency = specification.interval.replace("m", "min")
        symbol = f"{specification.symbol}.{_SUFFIXES.get(specification.exchange, '')}"
        if (
            row["exchange"] != specification.exchange
            or row["product"] != specification.product
            or row["dataset"] != frequency
            or row["scope"] != symbol
            or row["parameters"].get("freq") != frequency
            or row["parameters"].get("ts_code") != symbol
        ):
            raise ValueError("fixed receipt differs from the requested contract or native interval")
        manifest = json.loads(library._files.read(row["manifest_hash"], row["manifest_bytes"]))
        if (
            manifest["source"]
            != {"content_hash": row["source_hash"], "byte_count": row["source_bytes"]}
            or manifest["dataset"] != row["dataset"]
            or manifest["scope"] != row["scope"]
            or manifest["parameters"] != row["parameters"]
            or manifest["parquet"]["content_hash"] != row["parquet_hash"]
            or manifest["parquet"]["byte_count"] != row["parquet_bytes"]
            or manifest["quality"] != row["quality"]
        ):
            raise ValueError("fixed receipt and publication evidence disagree")
        raw = library._files.read(row["parquet_hash"], row["parquet_bytes"])
        table = pq.read_table(io.BytesIO(raw))
        if table.num_rows != row["row_count"] or not 0 < table.num_rows < 8000:
            raise ValueError("fixed minute receipt has invalid or truncated row count")
        content = _session_rows(table.to_pylist(), specification, symbol, label_convention)
        interpretation = json.dumps(
            {
                "rule": RULE,
                "receipt": str(receipt_id),
                "manifest": row["manifest_hash"],
                "labels": label_convention,
                "reference": interpretation_reference,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        spec = replace(specification, source_name="TUSHARE", source_reference=interpretation)
        return library.submit(
            content,
            filename=f"tushare-{receipt_id}-{spec.interval}.csv",
            source_name="TUSHARE",
            use_basis="Confirmed personal research subscription and local retention.",
            allow_retention=True,
            allow_download=False,
            spec=spec.to_mapping(),
            request_id=str(request_id),
            input_kind="CONVERTED_CSV",
            upstream_source_id=source_id,
            transformation_note=interpretation,
        )


def _session_rows(
    rows: list[dict[str, Any]], spec: ImportSpec, symbol: str, convention: str
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        ["event_time", "available_at", "source_record_id", "open", "high", "low", "close", "volume"]
    )
    selected: dict[datetime, list[str]] = {}
    for row in rows:
        if row.get("ts_code") != symbol:
            raise ValueError("fixed receipt contains another contract")
        if row.get("observation_status") == "ZERO_VOLUME":
            raise ValueError("zero-volume observation is not an executable research bar")
        label = (
            datetime.strptime(row["trade_time"], "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            .astimezone(UTC)
        )
        if convention == "BAR_END" and label == spec.session_open:
            raise ValueError("opening-time record has no complete preceding bar in this session")
        start = label if convention == "BAR_START" else label - spec.duration
        if start >= spec.session_close or start + spec.duration <= spec.session_open:
            continue
        if start < spec.session_open or start + spec.duration > spec.session_close:
            raise ValueError("provider bar crosses the declared session boundary")
        if start in selected:
            raise ValueError("provider receipt repeats a session bar")
        selected[start] = [
            start.isoformat().replace("+00:00", "Z"),
            (start + spec.duration).isoformat().replace("+00:00", "Z"),
            f"{symbol}:{spec.interval}:{row['trade_time']}",
            *(
                normalization.decimal_text(row[field])
                for field in ("open", "high", "low", "close", "vol")
            ),
        ]
    expected = {
        spec.session_open + spec.duration * offset
        for offset in range(int((spec.session_close - spec.session_open) / spec.duration))
    }
    if set(selected) != expected:
        raise ValueError("provider receipt does not contain exactly the declared session bars")
    writer.writerows(selected[key] for key in sorted(selected))
    return output.getvalue().encode()
