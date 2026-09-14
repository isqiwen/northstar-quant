"""One immutable ZIP delivery file per fully verified retired real contract."""

import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from northstar_quant import code_revision

from ..files import SourceFiles
from ..maintenance import library_write
from ..publications import PublishedDatasets
from ..tushare.contract_review import review_connection
from .lifecycle import completed


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
            raise ValueError("整合约尚未通过全部数据集的完整性验收，禁止发布数据包")
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
        ]
        from ..tushare.store import serial

        manifest = dict(
            rule="closed-contract-package/1",
            scope=scope,
            exchange=contract["exchange"],
            product=contract["product"],
            listing_date=lifetime.start.isoformat(),
            delisting_date=lifetime.end.isoformat(),
            code_revision=code_revision(),
            quality=result,
            inputs=[serial(r) for r in inputs],
        )
        files = SourceFiles.from_environment()
        artifact = write_package(PublishedDatasets.from_environment().root, manifest, files)
        c.execute(
            text("""INSERT INTO data_contract_publications
            (publication_id,scope,manifest,package_hash,package_bytes,path)
            VALUES(:id,:scope,CAST(:manifest AS jsonb),:hash,:bytes,:path)
            ON CONFLICT(publication_id) DO NOTHING"""),
            dict(
                id=artifact["publication_id"],
                scope=scope,
                manifest=_json(manifest).decode(),
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


def write_package(root: Path, manifest: dict[str, Any], source: SourceFiles) -> dict[str, Any]:
    """Physical writer used only after admission; hashing includes all pinned inputs."""
    labels = [manifest[k] for k in ("exchange", "product", "scope")]
    if any(
        not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", v) or v in {".", ".."}
        for v in labels
    ):
        raise ValueError("合约层级包含非法路径")
    identity = hashlib.sha256(_json(manifest)).hexdigest()
    directory = root.joinpath(*labels)
    for parent in (root, *[root.joinpath(*labels[:i]) for i in range(1, 4)]):
        if parent.is_symlink():
            raise ValueError("合约发布路径不能包含符号链接")
        parent.mkdir(exist_ok=True)
    destination = directory / f"{identity}.zip"
    descriptor, temporary = tempfile.mkstemp(prefix=".package-", dir=directory)
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", allowZip64=True) as archive:
                entries: dict[str, tuple[str, int]] = {}
                for item in manifest["inputs"]:
                    for role, suffix in (
                        ("source", "json"),
                        ("manifest", "json"),
                        ("parquet", "parquet"),
                    ):
                        digest, size = item[f"{role}_hash"], item[f"{role}_bytes"]
                        name = f"{role}/{digest}.{suffix}"
                        entries[name] = (digest, size)

                def put(name: str, content: bytes) -> None:
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.external_attr = 0o100644 << 16
                    info.compress_type = zipfile.ZIP_STORED
                    archive.writestr(info, content)

                put("manifest.json", _json(manifest))
                for name, (digest, size) in sorted(entries.items()):
                    put(name, source.read(digest, size))
            stream.flush()
            os.fsync(stream.fileno())
        digest = _hash(Path(temporary))
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or _hash(destination) != digest:
                raise ValueError("固定合约包已存在且内容不一致") from None
        SourceFiles._sync(directory)
        return dict(
            publication_id=identity,
            sha256=digest,
            bytes=destination.stat().st_size,
            path=str(destination.relative_to(root)),
        )
    finally:
        Path(temporary).unlink(missing_ok=True)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_packages(connection: Any, root: Path) -> None:
    for item in connection.execute(
        text("SELECT path,package_hash,package_bytes FROM data_contract_publications")
    ).mappings():
        path = root / item["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("合约包路径不属于发布目录")
        if path.stat().st_size != item["package_bytes"] or _hash(path) != item["package_hash"]:
            raise ValueError("合约发布包完整性检查失败")


def restore_packages(connection: Any, root: Path, source: SourceFiles) -> None:
    for item in connection.execute(text("SELECT * FROM data_contract_publications")).mappings():
        artifact = write_package(root, item["manifest"], source)
        if (artifact["sha256"], artifact["bytes"], artifact["path"]) != (
            item["package_hash"],
            item["package_bytes"],
            item["path"],
        ):
            raise ValueError("恢复的合约包与固定发布身份不一致")
