"""Provider-independent, immutable Hive partitions with bounded spill/encoding."""

import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..files import SourceFiles

NUMERIC = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
        "turnover_cny",
        "settlement_price",
        "previous_settlement_price",
        "contract_multiplier",
        "price_tick",
        "fee_rate_raw",
        "fee_amount_raw",
        "delivery_fee_raw",
        "close_today_fee_raw",
        "long_margin_raw",
        "short_margin_raw",
        "long_hedge_margin_raw",
        "short_hedge_margin_raw",
        "upper_limit",
        "lower_limit",
        "minimum_margin_raw",
        "long_positions",
        "short_positions",
        "warehouse_volume",
    }
)


def json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()


def safe_path(root: Path, relative: str) -> Path:
    parts = Path(relative).parts
    if (
        not parts
        or Path(relative).is_absolute()
        or any(p in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_=.-]+", p) for p in parts)
    ):
        raise ValueError("非法目录路径")
    # Check every component, including the configured root, before resolve/read/write.
    for parent in (root, *root.parents):
        if parent.is_symlink():
            raise ValueError("目录不能包含符号链接")
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("目录不能包含符号链接")
    return cursor


def put(root: Path, relative: str, content: bytes) -> None:
    path = safe_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".catalog-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError("不可变目录内容不一致") from None
        SourceFiles._sync(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def checked(root: Path, item: Any) -> bytes:
    path = safe_path(root, item["path"])
    if not 0 < item["bytes"] <= 5 * 1024 * 1024:
        raise ValueError("目录文件大小超过有界读取限制")
    try:
        with path.open("rb") as stream:
            raw = stream.read(item["bytes"] + 1)
    except FileNotFoundError:
        raise ValueError("快照目录文件丢失") from None
    if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("目录文件完整性检查失败")
    return raw


def materialize(
    root: Path, source: SourceFiles, records: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Spill identities to disposable SQLite, then write at most 64k rows per file.

    This is processing scratch space, not a second catalog. Conflicting observations
    reject the whole snapshot; equal duplicates coalesce independent of input order.
    """
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    entries = []
    with tempfile.TemporaryDirectory(prefix="northstar-catalog-") as temporary:
        with sqlite3.connect(str(Path(temporary) / "rows.sqlite")) as spill:
            spill.execute(
                "CREATE TABLE rows(partition TEXT,identity TEXT,domain TEXT,value BLOB, "
                "PRIMARY KEY(partition,identity))"
            )
            for record in records:
                partition = record["partition"]
                safe_path(root, partition)
                raw = json_bytes(record["values"])
                identity = json_bytes(record["identity"]).decode()
                previous = spill.execute(
                    "SELECT value FROM rows WHERE partition=? AND identity=?", (partition, identity)
                ).fetchone()
                if previous is not None and previous[0] != raw:
                    raise ValueError("同一领域记录存在冲突，拒绝发布")
                spill.execute(
                    "INSERT OR IGNORE INTO rows VALUES(?,?,?,?)",
                    (partition, identity, record["domain"], raw),
                )
            spill.commit()
            partitions = spill.execute(
                "SELECT DISTINCT partition,domain FROM rows ORDER BY partition"
            ).fetchall()
            for partition, domain in partitions:
                cursor = spill.execute(
                    "SELECT value FROM rows WHERE partition=? ORDER BY identity", (partition,)
                )
                while batch := cursor.fetchmany(64000):
                    values = [json.loads(row[0]) for row in batch]
                    columns = sorted({name for value in values for name in value})

                    def write(part: list[dict[str, Any]]) -> None:
                        table = pa.table(
                            {
                                name: pa.array(
                                    [
                                        None
                                        if r.get(name) is None
                                        else r[name]
                                        if name == "is_open"
                                        else Decimal(str(r[name]))
                                        if name in NUMERIC
                                        else str(r[name])
                                        for r in part
                                    ],
                                    type=(
                                        pa.bool_()
                                        if name == "is_open"
                                        else pa.decimal128(38, 12)
                                        if name in NUMERIC
                                        else pa.string()
                                    ),
                                )
                                for name in columns
                            }
                        )
                        stream = io.BytesIO()
                        pq.write_table(table, stream, compression="zstd", row_group_size=2048)
                        content = stream.getvalue()
                        if len(content) > source.max_file_bytes:
                            if len(part) < 2:
                                raise ValueError("单条标准记录超过文件上限")
                            middle = len(part) // 2
                            write(part[:middle])
                            write(part[middle:])
                            return
                        saved = source.store(content)
                        relative = f"{partition}/part-{saved.content_hash}.parquet"
                        put(root, relative, content)
                        entries.append(
                            dict(
                                domain=domain,
                                path=relative,
                                sha256=saved.content_hash,
                                bytes=saved.byte_count,
                                rows=len(part),
                                columns=columns,
                            )
                        )

                    write(values)
    return sorted(entries, key=lambda e: e["path"])
