"""One fenced downloader; persisted retries and atomic coverage/receipt commits."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from northstar_quant import code_revision

from ..library import DataLibrary
from ..maintenance import library_write
from . import acquisition, coverage, credentials, planning, publication
from .catalog import BY_KEY
from .quality import Empty, InvalidResponse, Truncated, closed_interval_evidence, normalize
from .store import initialize, job, serial, settings

__all__ = ["initialize", "process_next"]
_LOCK = 0x4E53515453594E


def process_next(library: DataLibrary) -> dict[str, Any] | None:
    engine = library._engine
    with library_write(engine), engine.begin() as ownership:
        if not ownership.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _LOCK}):
            return None
        config = settings(engine)
        if not config["enabled"]:
            return None
        with engine.begin() as connection:
            connection.execute(
                text("""UPDATE data_sync_attempts SET finished_at=now(),outcome='INTERRUPTED'
                WHERE finished_at IS NULL""")
            )
            connection.execute(
                text("""UPDATE data_sync_jobs SET status='PENDING',generation=NULL,
                error='进程中断，继续未提交分片' WHERE status='RUNNING'""")
            )
        planning.refresh(engine)
        planning.plan(engine)
        with engine.begin() as connection:
            # Every fifth request gives old history a turn even while new data arrives.
            count = connection.scalar(text("SELECT count(*) FROM data_sync_attempts")) or 0
            order = "start_at ASC" if count % 5 == 4 else "start_at DESC"
            row = (
                connection.execute(
                    text(f"""SELECT * FROM data_sync_jobs j
                WHERE status IN ('PENDING','WAITING') AND next_at<=now()
                AND (source_generation IS NOT NULL OR :download_ready)
                AND (source_generation IS NOT NULL OR NOT EXISTS (SELECT 1 FROM data_sync_jobs b
                WHERE b.dataset=j.dataset AND
                b.status='BLOCKED' AND b.error LIKE 'Tushare 权限%'))
                ORDER BY (source_generation IS NOT NULL) DESC,
                CASE dataset WHEN 'contracts' THEN 0 WHEN 'calendar' THEN 1 ELSE 2 END,
                {order},created_at LIMIT 1 FOR UPDATE SKIP LOCKED"""),
                    {
                        "download_ready": datetime.fromisoformat(config["next_request_at"])
                        <= datetime.now(UTC)
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            selected = serial(row)
            generation = uuid4()
            connection.execute(
                text("""UPDATE data_sync_jobs SET status='RUNNING',generation=:g,
                attempts=attempts+CASE WHEN source_generation IS NULL THEN 1 ELSE 0 END,
                updated_at=now() WHERE request_id=:id"""),
                {"g": generation, "id": selected["request_id"]},
            )
            connection.execute(
                text("""INSERT INTO data_sync_attempts
                    (generation,request_id,parent_generation,code_revision)
                    VALUES(:g,:id,:parent,:revision)"""),
                {
                    "g": generation,
                    "id": selected["request_id"],
                    "parent": selected["source_generation"],
                    "revision": code_revision(),
                },
            )
            if not selected["source_generation"]:
                connection.execute(
                    text("UPDATE data_sync_settings SET next_request_at=now()+:delay"),
                    {"delay": timedelta(seconds=60 / config["requests_per_minute"])},
                )
        selected["generation"] = generation
        selected["attempts"] += int(not selected["source_generation"])
        stage = "download"
        try:
            if selected["source_generation"]:
                stage = "source"
                with engine.connect() as connection:
                    source = (
                        connection.execute(
                            text(
                                "SELECT source_hash,source_bytes FROM data_sync_attempts "
                                "WHERE generation=:g AND request_id=:id"
                            ),
                            {"g": selected["source_generation"], "id": selected["request_id"]},
                        )
                        .mappings()
                        .one()
                    )
                content = library._files.read(source["source_hash"], source["source_bytes"])
            else:
                content = acquisition.fetch(
                    BY_KEY[selected["dataset"]].api, selected["parameters"], credentials.read()
                )
            stage = "storage"
            archived = library._files.store(content)
            with engine.begin() as connection:
                library.retain_tushare_response(
                    connection, generation, archived.content_hash, archived.byte_count
                )
                connection.execute(
                    text("""UPDATE data_sync_attempts SET source_hash=:hash,source_bytes=:size
                    WHERE generation=:g"""),
                    {"hash": archived.content_hash, "size": archived.byte_count, "g": generation},
                )
            stage = "quality"
            try:
                rows, quality = normalize(content, selected)
            except Empty:
                if not coverage.confirmed_empty(engine, selected):
                    raise
                rows, quality = [], closed_interval_evidence(selected["dataset"])
            coverage.verify(engine, selected, rows, quality)
            stage = "storage"
            artifact = publication.publish(
                rows,
                quality,
                selected,
                library._files,
                source={"content_hash": archived.content_hash, "byte_count": archived.byte_count},
            )
            stage = "commit"
            _commit(
                engine,
                selected,
                rows,
                quality,
                archived.content_hash,
                archived.byte_count,
                artifact,
            )
        except (Truncated, acquisition.ResponseLimit):
            with engine.begin() as connection:
                current = connection.scalar(
                    text("SELECT generation FROM data_sync_jobs WHERE request_id=:id FOR UPDATE"),
                    {"id": selected["request_id"]},
                )
                if current == generation:
                    divided = (
                        not selected["source_generation"]
                        and bool(selected["start_at"])
                        and planning.split(connection, selected)
                    )
                    _finish(
                        connection,
                        selected,
                        "SPLIT" if divided else "BLOCKED",
                        "达到接口上限，已拆分区间"
                        if divided
                        else "最小分片或合约目录达到接口上限，需核查供应商覆盖",
                    )
        except Empty as error:
            recent = (
                bool(selected["end_at"])
                and selected["end_at"] >= (planning.target_day() - timedelta(days=10)).isoformat()
            )
            _fail(
                engine,
                selected,
                str(error),
                retry=not selected["source_generation"],
                waiting=recent,
            )
        except acquisition.DownloadError as error:
            _fail(engine, selected, str(error), retry=error.retry)
        except InvalidResponse as error:
            _fail(engine, selected, f"{error}；原文已留存", retry=False)
        except (ValueError, OSError, LookupError):
            reason = {
                "source": "已留存原文缺失或损坏；重处理已停止，不重新下载替代原文",
                "download": "凭据配置不可用，请在界面重新保存 token",
                "storage": (
                    "持久存储不可用或容量不足；自动同步已暂停，请检查存储目录、权限与可用空间"
                ),
                "quality": "字段、范围、重复或量价校验失败；原文已留存",
                "commit": "目录登记失败；原文已留存，请检查来源字段",
            }[stage]
            _fail(engine, selected, reason, retry=False)
            if stage in ("download", "storage"):
                with engine.begin() as connection:
                    connection.execute(
                        text("UPDATE data_sync_settings SET enabled=false,error=:error"),
                        {"error": reason},
                    )
        return job(engine, UUID(selected["request_id"]))


def _finish(
    connection: Any,
    selected: dict[str, Any],
    status: str,
    error: str | None = None,
    delay: timedelta = timedelta(),
) -> None:
    connection.execute(
        text("""UPDATE data_sync_jobs SET status=:status,error=:error,
        next_at=now()+:delay,updated_at=now(),
        source_generation=CASE WHEN :status='WAITING' THEN source_generation ELSE NULL END
        WHERE request_id=:id AND generation=:g"""),
        {
            "status": status,
            "error": error,
            "delay": delay,
            "id": selected["request_id"],
            "g": selected["generation"],
        },
    )
    connection.execute(
        text("""UPDATE data_sync_attempts SET finished_at=now(),outcome=:status,error=:error
        WHERE generation=:g"""),
        {"status": status, "error": error, "g": selected["generation"]},
    )


def _fail(
    engine: Engine, selected: dict[str, Any], reason: str, *, retry: bool, waiting: bool = False
) -> None:
    allowed = retry and (waiting or selected["attempts"] < 6)
    delay = timedelta(
        seconds=max(3600 if waiting else 30, min(21600, 30 * 2 ** min(selected["attempts"], 10)))
    )
    with engine.begin() as connection:
        _finish(connection, selected, "WAITING" if allowed else "BLOCKED", reason, delay)


def _commit(
    engine: Engine,
    selected: dict[str, Any],
    rows: list[dict[str, Any]],
    quality: dict[str, Any],
    source_hash: str,
    source_bytes: int,
    artifact: dict[str, Any],
) -> None:
    with engine.begin() as connection:
        generation = connection.scalar(
            text("SELECT generation FROM data_sync_jobs WHERE request_id=:id FOR UPDATE"),
            {"id": selected["request_id"]},
        )
        if generation != selected["generation"]:
            return
        receipt = connection.scalar(
            text("""INSERT INTO data_sync_receipts
            (receipt_id,request_id,source_hash,source_bytes,content_hash,row_count,
             manifest_hash,manifest_bytes,parquet_hash,parquet_bytes,quality,code_revision)

            VALUES(:receipt,:id,:source,:bytes,:hash,:rows,:manifest,:manifest_bytes,
                :parquet_hash,:parquet_bytes,CAST(:quality AS jsonb),:revision)
            ON CONFLICT(request_id,content_hash) DO NOTHING RETURNING receipt_id"""),
            {
                "receipt": uuid4(),
                "id": selected["request_id"],
                "source": source_hash,
                "bytes": source_bytes,
                "hash": quality["content_hash"],
                "rows": len(rows),
                "manifest": artifact["content_hash"],
                "manifest_bytes": artifact["byte_count"],
                "parquet_hash": artifact["parquet_hash"],
                "parquet_bytes": artifact["parquet_bytes"],
                "quality": json.dumps(quality),
                "revision": code_revision(),
            },
        )
        if receipt is None:
            receipt = connection.scalar(
                text(
                    "SELECT receipt_id FROM data_sync_receipts WHERE request_id=:id AND "
                    "content_hash=:hash"
                ),
                {"id": selected["request_id"], "hash": quality["content_hash"]},
            )
        connection.execute(
            text("""INSERT INTO data_sync_coverage(request_id,receipt_id)
            VALUES(:id,:receipt) ON CONFLICT(request_id) DO UPDATE
            SET receipt_id=EXCLUDED.receipt_id,checked_at=now()"""),
            {"id": selected["request_id"], "receipt": receipt},
        )
        connection.execute(
            text(
                "UPDATE data_sync_jobs SET receipt_id=:receipt,checked_at=now() WHERE "
                "request_id=:id"
            ),
            {"id": selected["request_id"], "receipt": receipt},
        )
        if selected["dataset"] == "contracts":
            for row in rows:
                if not row.get("fut_code") or not row.get("exchange"):
                    raise ValueError("合约目录缺少品种或交易所")
                connection.execute(
                    text("""INSERT INTO data_sync_contracts(ts_code,exchange,product,kind,details)
                    VALUES(:code,:exchange,:product,:kind,CAST(:details AS jsonb))
                    ON CONFLICT(ts_code) DO UPDATE SET details=EXCLUDED.details"""),
                    {
                        "code": row["ts_code"],
                        "exchange": row["exchange"],
                        "product": row["fut_code"],
                        "kind": selected["parameters"]["fut_type"],
                        "details": json.dumps(row),
                    },
                )
        if selected["dataset"] == "calendar":
            for row in rows:
                connection.execute(
                    text("""INSERT INTO data_sync_calendar(exchange,cal_date,is_open)
                    VALUES(:exchange,CAST(:day AS date),:open)
                    ON CONFLICT(exchange,cal_date) DO UPDATE SET is_open=EXCLUDED.is_open"""),
                    {
                        "exchange": row["exchange"],
                        "day": row["cal_date"],
                        "open": row["is_open"] == 1,
                    },
                )
        connection.execute(
            text("UPDATE data_sync_attempts SET receipt_id=:receipt WHERE generation=:g"),
            {"receipt": receipt, "g": selected["generation"]},
        )
        _finish(connection, selected, "VALIDATED")
