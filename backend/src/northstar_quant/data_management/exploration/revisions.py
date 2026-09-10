"""Deterministic differences between two explicitly pinned response publications."""

import hashlib
import io
import json
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from ..tushare import normalization, publication
from ..tushare.catalog import BY_KEY
from ..tushare.store import serial
from .catalog import PRICE_DATASETS

RULE = "response-diff/1"


def _rows(receipt: dict[str, Any]) -> dict[tuple[str, ...], dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    files = publication.storage()
    manifest = json.loads(files.read(receipt["manifest_hash"], receipt["manifest_bytes"]))
    if (
        manifest["quality"]["content_hash"] != receipt["content_hash"]
        or manifest["dataset"] != receipt["dataset"]
        or manifest["scope"] != receipt["scope"]
        or manifest["parquet"]
        != {"content_hash": receipt["parquet_hash"], "byte_count": receipt["parquet_bytes"]}
    ):
        raise ValueError("发布清单与固定版本身份不一致")
    raw = files.read(receipt["parquet_hash"], receipt["parquet_bytes"])
    parquet = pq.ParquetFile(io.BytesIO(raw))
    definition = BY_KEY[receipt["dataset"]]
    if (
        parquet.metadata.num_rows != receipt["row_count"]
        or parquet.metadata.num_rows >= definition.limit
    ):
        raise ValueError("固定版本行数与目录或接口上限不一致")
    result = {}
    for value in parquet.read().to_pylist():
        row = normalization.response_row(value)
        key = tuple(str(row[field]) for field in definition.identity)
        if key in result:
            raise ValueError("固定版本包含重复记录身份")
        result[key] = row
    return result


def compare(engine: Engine, *, before_id: UUID, after_id: UUID, offset: int = 0) -> dict[str, Any]:
    if before_id == after_id or not 0 <= offset <= 1000000:
        raise ValueError("请选择两个不同的固定版本及有效页码")
    with engine.connect() as c:
        found = (
            c.execute(
                text("""SELECT r.*,j.dataset,j.scope FROM data_sync_receipts r
            JOIN data_sync_jobs j USING(request_id)
            WHERE r.receipt_id=ANY(CAST(:ids AS uuid[]))"""),
                {"ids": [before_id, after_id]},
            )
            .mappings()
            .all()
        )
    by_id = {r["receipt_id"]: dict(r) for r in found}
    if set(by_id) != {before_id, after_id}:
        raise LookupError("固定发布版本不存在")
    before, after = by_id[before_id], by_id[after_id]
    if before["request_id"] != after["request_id"] or before["dataset"] not in PRICE_DATASETS:
        raise ValueError("只能比较同一行情请求分片的两个版本")
    left, right = _rows(before), _rows(after)
    identity = BY_KEY[before["dataset"]].identity
    counts = dict(added=0, removed=0, changed=0, unchanged=0)
    page = []
    total = 0
    for key in sorted(left.keys() | right.keys()):
        old, new = left.get(key, {}), right.get(key, {})
        kind = "added" if key not in left else "removed" if key not in right else "changed"
        fields = [
            f
            for f in sorted(old.keys() | new.keys())
            if (f in old) != (f in new) or old.get(f) != new.get(f)
        ]
        counts[kind if fields else "unchanged"] += 1
        for field in fields:
            if offset <= total < offset + 100:
                # Long supplier text is explicitly previewed; never silently round money.
                old_value, new_value = old.get(field), new.get(field)
                page.append(
                    {
                        "identity": dict(zip(identity, key, strict=True)),
                        "kind": kind,
                        "field": field,
                        "before_present": field in old,
                        "after_present": field in new,
                        "before": old_value[:512] if isinstance(old_value, str) else old_value,
                        "after": new_value[:512] if isinstance(new_value, str) else new_value,
                        "preview": any(
                            isinstance(v, str) and len(v) > 512 for v in (old_value, new_value)
                        ),
                    }
                )
            total += 1

    def evidence(r: dict[str, Any]) -> dict[str, Any]:
        return serial(
            {
                f: r[f]
                for f in ("receipt_id", "source_hash", "manifest_hash", "quality", "code_revision")
            }
        )

    return {
        "comparison_id": hashlib.sha256(
            json.dumps([RULE, before["manifest_hash"], after["manifest_hash"]]).encode()
        ).hexdigest(),
        "rule": RULE,
        "before": evidence(before),
        "after": evidence(after),
        "source_changed": before["source_hash"] != after["source_hash"],
        "rules_changed": any(
            before["quality"].get(k) != after["quality"].get(k) for k in ("rule", "normalization")
        ),
        "counts": counts,
        "changes": page,
        "total": total,
        "offset": offset,
        "note": (
            "按两个固定版本的记录身份比较；表示观察到的修订，不证明历史首次可得时间。"
            "每页100项字段变化；长文本明确标记预览。"
        ),
    }
