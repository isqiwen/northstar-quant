"""Durable, explicitly selected physical compactions; original receipts are retained."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Connection,
    Engine,
    MetaData,
    String,
    Table,
    insert,
    select,
    text,
    update,
)

from northstar_quant import code_revision
from northstar_quant.persistence.sql import UTCDateTime

from .exploration import catalog, rows
from .files import SourceFiles
from .maintenance import library_write
from .tushare import normalization, publication
from .tushare.store import serial

_metadata = MetaData()
_jobs = Table(
    "data_compactions",
    _metadata,
    Column("compaction_id", String(36), primary_key=True),
    Column("plan_id", String(64), nullable=False),
    Column("plan", JSON, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("status", String(16), nullable=False),
    Column("result", JSON),
    Column("error", String(1000)),
    CheckConstraint("status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')"),
    CheckConstraint(
        "(status='SUCCEEDED' AND result IS NOT NULL AND error IS NULL) "
        "OR (status<>'SUCCEEDED' AND result IS NULL)"
    ),
)
_LOCK = 0x4E53434F4D5043


def content_id(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def initialize(c: Connection) -> None:
    _metadata.create_all(c)
    c.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION compaction_transition() RETURNS trigger AS $$
        BEGIN
          IF TG_OP='DELETE' OR OLD.status IN ('SUCCEEDED','FAILED')
             OR ROW(OLD.compaction_id,OLD.plan_id,OLD.plan::jsonb,OLD.created_at)
                IS DISTINCT FROM ROW(NEW.compaction_id,NEW.plan_id,NEW.plan::jsonb,NEW.created_at)
             OR NOT ((OLD.status='PENDING' AND NEW.status='RUNNING')
                      OR (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED')))
          THEN RAISE EXCEPTION 'compaction identities and terminal facts are immutable'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON data_compactions;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_compactions
          FOR EACH ROW EXECUTE FUNCTION compaction_transition();
    """)


def submit(
    engine: Engine,
    request_id: UUID,
    *,
    dataset: str,
    scope: str,
    start: str,
    end: str,
    receipt_ids: list[UUID],
) -> dict[str, Any]:
    if not 2 <= len(set(receipt_ids)) == len(receipt_ids) <= 32:
        raise ValueError("请选择 2–32 个固定版本进行合并")
    versions = catalog.pinned(engine, dataset, scope, start, end, receipt_ids)
    if sum(v["parquet_bytes"] for v in versions) > 32 * 1024**2:
        raise ValueError("合并输入超过 32 MiB，请缩小范围")
    plan = {
        "revision": "fixed-range-compaction/1",
        "code_revision": code_revision(),
        "binding": {
            "dataset": dataset,
            "scope": scope,
            "start": start,
            "end": end,
            "receipt_ids": [str(v["receipt_id"]) for v in versions],
        },
        "inputs": [serial(v) for v in versions],
    }
    with library_write(engine), engine.begin() as c:
        old = (
            c.execute(select(_jobs).where(_jobs.c.compaction_id == str(request_id)))
            .mappings()
            .first()
        )
        if old is not None:
            if old["plan_id"] != content_id(plan):
                raise ValueError("合并请求身份已用于不同固定输入")
        else:
            c.execute(
                insert(_jobs).values(
                    compaction_id=str(request_id),
                    plan_id=content_id(plan),
                    plan=plan,
                    created_at=datetime.now(UTC),
                    status="PENDING",
                )
            )
    return get(engine, str(request_id))


def get(engine: Engine, identity: str) -> dict[str, Any]:
    with engine.connect() as c:
        value = (
            c.execute(select(_jobs).where(_jobs.c.compaction_id == identity))
            .mappings()
            .one_or_none()
        )
    if value is None:
        raise LookupError("compaction not found")
    if content_id(value["plan"]) != value["plan_id"]:
        raise ValueError("compaction plan integrity failure")
    return serial(value)


def listing(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as c:
        ids = c.scalars(
            select(_jobs.c.compaction_id).order_by(_jobs.c.created_at.desc()).limit(100)
        ).all()
    return [get(engine, i) for i in ids]


def process_next(engine: Engine, archive: SourceFiles) -> dict[str, Any] | None:
    with library_write(engine), engine.begin() as owner:
        if not owner.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _LOCK}):
            return None
        with engine.begin() as c:
            c.execute(
                update(_jobs)
                .where(_jobs.c.status == "RUNNING")
                .values(status="FAILED", error="合并进程中断，原版本未改变；可提交新的合并请求")
            )
            identity = c.scalar(
                select(_jobs.c.compaction_id)
                .where(_jobs.c.status == "PENDING")
                .order_by(_jobs.c.created_at)
                .limit(1)
                .with_for_update()
            )
            if identity is None:
                return None
            c.execute(
                update(_jobs).where(_jobs.c.compaction_id == identity).values(status="RUNNING")
            )
        job = get(engine, identity)
        try:
            if job["plan"]["code_revision"] != code_revision():
                raise ValueError("合并计划实现版本与 worker 不同，请重新提交")
            binding = job["plan"]["binding"]
            query = {**binding, "receipt_ids": [UUID(v) for v in binding["receipt_ids"]]}
            result = rows.read(engine, **query, limit=20000)
            if result["versions"] != job["plan"]["inputs"]:
                raise ValueError("合并输入身份发生变化")
            if not result["rows"]:
                raise ValueError("所选固定范围没有记录，无需合并")
            result_files = _write(result["rows"], binding["dataset"], archive)
            manifest = {
                "revision": "fixed-range-compaction/1",
                "plan_id": job["plan_id"],
                "view": {
                    k: v for k, v in result.items() if k not in {"rows", "scan", "offset", "limit"}
                },
                "files": result_files,
            }
            raw = json.dumps(manifest, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            saved = archive.store(raw)
            publication.storage().store(raw)
            output = {
                "manifest": saved.to_dict(),
                "files": result_files,
                "total": result["total"],
                "view_id": result["view_id"],
            }
            with engine.begin() as c:
                c.execute(
                    update(_jobs)
                    .where(_jobs.c.compaction_id == identity)
                    .values(status="SUCCEEDED", result=output)
                )
        except Exception as error:
            with engine.begin() as c:
                c.execute(
                    update(_jobs)
                    .where(_jobs.c.compaction_id == identity)
                    .values(status="FAILED", error=str(error)[:1000])
                )
        return get(engine, identity)


def _write(
    values: list[dict[str, Any]], dataset: str, archive: SourceFiles
) -> list[dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    files = publication.storage()
    outputs = []

    def write_part(part: list[dict[str, Any]], offset: int) -> None:
        table = publication.response_table(
            [{k: v for k, v in r.items() if k not in {"_key", "_receipts"}} for r in part], dataset
        )
        table = table.append_column("_key", pa.array([r["_key"] for r in part], type=pa.string()))
        table = table.append_column(
            "_receipts", pa.array([r["_receipts"] for r in part], type=pa.list_(pa.string()))
        )
        stream = io.BytesIO()
        pq.write_table(table, stream, compression="zstd", row_group_size=512)
        raw = stream.getvalue()
        if len(raw) > archive.max_file_bytes:
            if len(part) == 1:
                raise ValueError("单条合并记录超过文件上限")
            middle = len(part) // 2
            write_part(part[:middle], offset)
            write_part(part[middle:], offset + middle)
            return
        saved = archive.store(raw)
        files.store(raw)
        outputs.append({**saved.to_dict(), "offset": offset, "rows": len(part)})

    for offset in range(0, len(values), 4096):
        write_part(values[offset : offset + 4096], offset)
    return outputs


def read(engine: Engine, identity: str, *, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    import pyarrow.parquet as pq

    if not 0 <= offset <= 20000 or not 1 <= limit <= 1000:
        raise ValueError("invalid compacted range page")
    job = get(engine, identity)
    if job["status"] != "SUCCEEDED":
        raise ValueError("合并尚未成功，继续使用原固定版本")
    files = publication.storage()
    reference = job["result"]["manifest"]
    manifest = json.loads(files.read(reference["content_hash"], reference["byte_count"]))
    if manifest["plan_id"] != job["plan_id"] or manifest["files"] != job["result"]["files"]:
        raise ValueError("compaction manifest identity mismatch")
    view = manifest["view"]
    binding = job["plan"]["binding"]
    current = catalog.pinned(
        engine, **{**binding, "receipt_ids": [UUID(v) for v in binding["receipt_ids"]]}
    )
    if [serial(v) for v in current] != job["plan"]["inputs"]:
        raise ValueError("compaction original receipt identity mismatch")
    sources, export_allowed = rows.source_permissions(engine, current)
    view = {**view, "sources": sources, "export_allowed": export_allowed}
    selected = []
    cost = {
        "files": 0,
        "verified_bytes": 0,
        "row_groups_total": 0,
        "row_groups_read": 0,
        "rows_decoded": 0,
        "selected_compressed_bytes": 0,
    }
    for entry in manifest["files"]:
        if entry["offset"] >= offset + limit or entry["offset"] + entry["rows"] <= offset:
            continue
        raw = files.read(entry["content_hash"], entry["byte_count"])
        parquet = pq.ParquetFile(io.BytesIO(raw))
        if parquet.metadata.num_rows != entry["rows"]:
            raise ValueError("compacted file row count mismatch")
        cost["files"] += 1
        cost["verified_bytes"] += len(raw)
        cost["row_groups_total"] += parquet.num_row_groups
        position = entry["offset"]
        for group_id in range(parquet.num_row_groups):
            group = parquet.metadata.row_group(group_id)
            if position + group.num_rows > offset and position < offset + limit:
                cost["row_groups_read"] += 1
                cost["rows_decoded"] += group.num_rows
                cost["selected_compressed_bytes"] += sum(
                    group.column(i).total_compressed_size for i in range(group.num_columns)
                )
                for index, row in enumerate(parquet.read_row_group(group_id).to_pylist(), position):
                    if offset <= index < offset + limit:
                        origins, key = row.pop("_receipts"), row.pop("_key")
                        selected.append(
                            {**normalization.response_row(row), "_key": key, "_receipts": origins}
                        )
            position += group.num_rows
    return {
        **view,
        "rows": selected,
        "offset": offset,
        "limit": limit,
        "scan": cost,
        "note": view["note"] + " 已选择固定合并清单；原始版本及逐行来源保留。",
    }


def references(c: Connection) -> list[dict[str, Any]]:
    result = []
    for row in c.execute(select(_jobs).where(_jobs.c.status == "SUCCEEDED")).mappings():
        for item in [row["result"]["manifest"], *row["result"]["files"]]:
            result.append(
                {
                    "source_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"compaction/{row['compaction_id']}/{item['content_hash']}",
                        )
                    ),
                    "content_hash": item["content_hash"],
                    "byte_count": item["byte_count"],
                }
            )
    return result
