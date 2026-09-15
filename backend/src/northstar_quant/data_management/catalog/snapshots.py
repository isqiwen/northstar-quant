"""Immutable domain catalog file manifests; no supplier or database dependency."""

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..files import SourceFiles
from .partitioned import checked, json_bytes, materialize, put


def write(
    root: Path,
    manifest: dict[str, Any],
    source: SourceFiles,
    records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    for key in ("scope", "exchange", "product"):
        value = manifest.get(key, "")
        if key != "scope" and not value:
            continue
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", value)
            or value in {".", ".."}
        ):
            raise ValueError("合约元数据包含非法路径")
    if "files" not in manifest:
        if records is None:
            raise ValueError("首次发布必须提供标准领域记录")

        manifest = {
            **manifest,
            "layout": "northstar-domain-catalog/1",
            "time_basis": "SUPPLIER_HISTORICAL_LABEL",
            "fee_basis": manifest.get("fee_basis", "PROVIDER_RAW_UNITS_NOT_EXECUTION_TERMS"),
            "files": materialize(root, source, records),
        }
    else:
        if manifest["layout"] != "northstar-domain-catalog/1":
            raise ValueError("未知领域目录格式")
        for entry in manifest["files"]:
            put(root, entry["path"], source.read(entry["sha256"], entry["bytes"]))
    content = json_bytes(manifest)
    saved = source.store(content)
    relative = f"published/snapshots/{saved.content_hash}.json"
    put(root, relative, content)
    return dict(
        publication_id=saved.content_hash,
        sha256=saved.content_hash,
        bytes=saved.byte_count,
        path=relative,
        manifest=manifest,
    )


def load(root: Path, snapshot_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise ValueError("无效快照身份")
    from .partitioned import safe_path

    relative = f"published/snapshots/{snapshot_id}.json"
    path = safe_path(root, relative)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise ValueError("快照目录文件丢失") from None
    raw = checked(root, dict(path=relative, sha256=snapshot_id, bytes=size))
    manifest: dict[str, Any] = json.loads(raw)
    if manifest.get("layout") != "northstar-domain-catalog/1":
        raise ValueError("未知领域目录格式")
    for entry in manifest["files"]:
        checked(root, entry)
    return manifest


def verify(root: Path, item: Any) -> None:
    raw = checked(
        root, dict(path=item["path"], sha256=item["manifest_hash"], bytes=item["manifest_bytes"])
    )
    manifest: dict[str, Any] = json.loads(raw)
    if manifest.get("layout") != "northstar-domain-catalog/1":
        raise ValueError("未知领域目录格式")
    for entry in manifest["files"]:
        checked(root, entry)
