"""Immutable contract snapshots over shared domain partitions, with private source pins."""

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from northstar_quant import code_revision

from ..files import SourceFiles
from ..maintenance import library_write
from ..publications import PublishedDatasets
from ..tushare.contract_review import review_connection
from .lifecycle import completed
from .partitioned import checked, json_bytes, materialize, put
from .requirements import classify, requirement


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def publish(engine: Engine, scope: str) -> dict[str, Any]:
    """No caller-supplied PASS flag: re-read owned evidence before any package exists."""
    with (
        library_write(engine),
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
        c.begin(),
    ):
        c.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,421))"), {"scope": scope}
        )
        contract = (
            c.execute(
                text("SELECT * FROM data_sync_contracts WHERE ts_code=:scope"), {"scope": scope}
            )
            .mappings()
            .one()
        )
        lifetime = completed(contract)
        result = review_connection(c, scope)
        if not result["admitted"]:
            raise ValueError("整合约尚未通过本类型必需数据的完整性验收，禁止发布数据包")
        inputs = [
            dict(r)
            for r in c.execute(
                text("""SELECT DISTINCT r.*,j.dataset,j.scope
            FROM data_contract_requests cr JOIN data_sync_jobs j USING(request_id)
            JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
            JOIN data_sync_coverage v ON v.request_id=j.request_id AND v.receipt_id=r.receipt_id
            WHERE cr.scope=:scope AND j.status='VALIDATED'
            ORDER BY j.dataset,j.scope,r.receipt_id"""),
                {"scope": scope},
            ).mappings()
            if requirement(classify(contract), r["dataset"]).collect
        ]
        from ..tushare.store import serial

        manifest = dict(
            rule="closed-contract-snapshot/1",
            scope=scope,
            exchange=contract["exchange"],
            product=contract["product"],
            listing_date=lifetime.start.isoformat(),
            last_trade_date=lifetime.end.isoformat(),
            last_delivery_date=lifetime.last_delivery.isoformat(),
            first_delivery_date=None,
            delivery_month=contract["details"].get("d_month"),
            reference=dict(
                name=contract["details"].get("name"),
                delivery_method=contract["details"].get("d_mode_desc"),
                contract_type=classify(contract).category,
                contract_multiplier=contract["details"].get("per_unit"),
                price_tick=None,
                contract_id=None,
            ),
            code_revision=code_revision(),
            quality=result,
            inputs=[serial(r) for r in inputs],
        )
        files = SourceFiles.from_environment()
        artifact = write_snapshot(PublishedDatasets.from_environment().root, manifest, files)
        c.execute(
            text("""INSERT INTO data_contract_publications
            (publication_id,scope,manifest,manifest_hash,manifest_bytes,path)
            VALUES(:id,:scope,CAST(:manifest AS jsonb),:hash,:bytes,:path)
            ON CONFLICT(publication_id) DO NOTHING"""),
            dict(
                id=artifact["publication_id"],
                scope=scope,
                manifest=_json(artifact["manifest"]).decode(),
                hash=artifact["sha256"],
                bytes=artifact["bytes"],
                path=artifact["path"],
            ),
        )
        c.execute(
            text(
                "UPDATE data_contract_collections SET status='PUBLISHED',updated_at=now() "
                "WHERE scope=:scope"
            ),
            {"scope": scope},
        )
        return artifact


def write_snapshot(root: Path, manifest: dict[str, Any], source: SourceFiles) -> dict[str, Any]:
    for key in ("exchange", "product", "scope"):
        value = manifest[key]
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", value)
            or value in {".", ".."}
        ):
            raise ValueError("合约元数据包含非法路径")
    if "files" not in manifest:
        from ..tushare.standard_rows import records

        manifest = {
            **manifest,
            "layout": "northstar-domain-catalog/1",
            "time_basis": "SUPPLIER_HISTORICAL_LABEL",
            "fee_basis": "PROVIDER_RAW_UNITS_NOT_EXECUTION_TERMS",
            "files": materialize(root, source, records(manifest, source)),
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


def verify_snapshot(root: Path, item: Any) -> None:
    raw = checked(
        root, dict(path=item["path"], sha256=item["manifest_hash"], bytes=item["manifest_bytes"])
    )
    manifest: dict[str, Any] = json.loads(raw)
    if manifest.get("layout") != "northstar-domain-catalog/1":
        raise ValueError("未知领域目录格式")
    for entry in manifest["files"]:
        checked(root, entry)


def verify_snapshots(connection: Any, root: Path) -> None:
    for item in connection.execute(
        text("SELECT path,manifest_hash,manifest_bytes FROM data_contract_publications")
    ).mappings():
        verify_snapshot(root, item)


def restore_snapshots(connection: Any, root: Path, source: SourceFiles) -> None:
    for item in connection.execute(text("SELECT * FROM data_contract_publications")).mappings():
        artifact = write_snapshot(root, item["manifest"], source)
        if (artifact["sha256"], artifact["bytes"], artifact["path"]) != (
            item["manifest_hash"],
            item["manifest_bytes"],
            item["path"],
        ):
            raise ValueError("恢复的快照与固定发布身份不一致")


def references(connection: Any, content_hashes: list[str] | None = None) -> list[dict[str, object]]:
    from uuid import NAMESPACE_URL, uuid5

    if content_hashes == []:
        return []
    result = []
    sql = "SELECT * FROM data_contract_publications"
    if content_hashes is not None:
        sql += """ WHERE manifest_hash=ANY(:hashes) OR EXISTS
            (SELECT 1 FROM jsonb_array_elements(manifest->'files') f
             WHERE f->>'sha256'=ANY(:hashes))"""
    for row in connection.execute(text(sql), {"hashes": content_hashes}).mappings():
        entries = [
            *row["manifest"]["files"],
            dict(path=row["path"], sha256=row["manifest_hash"], bytes=row["manifest_bytes"]),
        ]
        for entry in entries:
            if content_hashes is None or entry["sha256"] in content_hashes:
                result.append(
                    dict(
                        source_id=str(
                            uuid5(NAMESPACE_URL, f"{row['publication_id']}/{entry['path']}")
                        ),
                        content_hash=entry["sha256"],
                        byte_count=entry["bytes"],
                    )
                )
    return result
