#!/usr/bin/env python3
"""显式创建或验证 Northstar 的受限逻辑备份包。

这个维护入口不会自动运行，也不会写入 health readiness 证据。创建操作需要操作者
同时确认服务已静止；它只生成可导出的逻辑包，不能替代异机加密备份、WAL/PITR 或
已审批的生产恢复 runbook。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from northstar_quant.foundation.backup import (
    BackupBundleError,
    BackupBundleSources,
    PostgreSQLBackupError,
    create_backup_bundle,
    create_postgresql_dump,
    verify_backup_bundle,
    verify_postgresql_dump,
)
from northstar_quant.foundation.config.settings import load_settings
from northstar_quant.foundation.security import redact_text


_SERVICE_NAME = "northstar-quant.service"
_SAFE_POSIX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class MaintenanceBackupError(RuntimeError):
    """维护脚本的显式安全前置条件不满足。"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="创建或验证 Northstar 受限逻辑备份包。")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="创建新的不可覆盖备份包。")
    create.add_argument("--output-parent", type=Path, required=True, help="已挂载的外部备份目录。")
    create.add_argument(
        "--confirm-create",
        default="",
        help="必须精确传入 YES，确认这是一次显式备份创建。",
    )
    create.add_argument(
        "--confirm-runtime-quiesced",
        default="",
        help="必须精确传入 YES；脚本也会检查固定服务处于 inactive。",
    )

    verify = commands.add_parser("verify", help="重新验证已有备份包，不连接数据库。")
    verify.add_argument("--bundle-dir", type=Path, required=True, help="待验证的备份包目录。")
    return parser


def _release_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise MaintenanceBackupError(f"{label}不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
        mode = path.lstat().st_mode
    except OSError as exc:
        raise MaintenanceBackupError(f"{label}不存在或无法安全读取。") from exc
    if not stat.S_ISREG(mode):
        raise MaintenanceBackupError(f"{label}必须是普通文件。")
    return resolved


def _require_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise MaintenanceBackupError(f"{label}不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
        mode = path.lstat().st_mode
    except OSError as exc:
        raise MaintenanceBackupError(f"{label}不存在或无法安全读取。") from exc
    if not stat.S_ISDIR(mode):
        raise MaintenanceBackupError(f"{label}必须是目录。")
    return resolved


def _assert_service_is_inactive() -> None:
    environment = {"PATH": os.environ.get("PATH", "") if os.name == "nt" else _SAFE_POSIX_PATH}
    if not environment["PATH"]:
        raise MaintenanceBackupError("无法建立受限 systemctl 环境。")
    try:
        result = subprocess.run(
            ["systemctl", "show", "--property=ActiveState", "--value", _SERVICE_NAME],
            check=False,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except OSError as exc:
        raise MaintenanceBackupError("无法查询固定 Northstar systemd 服务状态。") from exc
    except subprocess.TimeoutExpired as exc:
        raise MaintenanceBackupError("查询固定 Northstar systemd 服务状态超时。") from exc
    if result.returncode != 0 or result.stdout.strip() != "inactive":
        raise MaintenanceBackupError(
            "固定 northstar-quant.service 未确认处于 inactive；拒绝复制运行状态。"
        )


def _assert_external_output_parent(output_parent: Path, sources: tuple[Path, ...]) -> Path:
    parent = _require_directory(output_parent, "备份输出父目录")
    parent_mode = parent.lstat().st_mode
    if os.name == "posix" and parent_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise MaintenanceBackupError("备份输出父目录不能允许 group 或 other 写入。")
    for source in sources:
        resolved_source = source.resolve(strict=True)
        if parent.is_relative_to(resolved_source) or resolved_source.is_relative_to(parent):
            raise MaintenanceBackupError("备份输出目录不能与任何备份输入目录重叠。")
    return parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_release_file(source: Path, destination: Path) -> None:
    _require_regular_file(source, "release metadata 文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)


def _snapshot_release_metadata(release_root: Path, destination: Path) -> Path:
    release = _require_directory(release_root, "活动 release 根目录")
    release_id = release.name
    if not _RELEASE_ID_PATTERN.fullmatch(release_id):
        raise MaintenanceBackupError("活动 release 标识不符合受限格式。")
    artifact_metadata = _require_regular_file(
        release / "DEPLOY_ARTIFACT_META.txt",
        "release artifact metadata",
    )
    systemd_dir = _require_directory(release / ".northstar" / "systemd", "release systemd 快照目录")
    snapshot_files = sorted(systemd_dir.glob("*.service"))
    if not snapshot_files:
        raise MaintenanceBackupError("活动 release 缺少 systemd 快照。")
    destination.mkdir(mode=0o700)
    _copy_release_file(artifact_metadata, destination / artifact_metadata.name)
    snapshot_hashes: dict[str, str] = {}
    for snapshot in snapshot_files:
        if snapshot.parent != systemd_dir:
            raise MaintenanceBackupError("release systemd 快照路径不受支持。")
        _copy_release_file(snapshot, destination / "systemd" / snapshot.name)
        snapshot_hashes[snapshot.name] = _sha256(snapshot)
    current_release = {
        "format_version": 1,
        "active_release_id": release_id,
        "artifact_metadata_sha256": _sha256(artifact_metadata),
        "systemd_snapshot_sha256": snapshot_hashes,
    }
    metadata_path = destination / "current-release.json"
    metadata_path.write_text(
        json.dumps(current_release, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(metadata_path, 0o600)
    return destination


def _create(args: argparse.Namespace) -> dict[str, object]:
    if args.confirm_create != "YES":
        raise MaintenanceBackupError("创建备份必须显式传入 --confirm-create YES。")
    if args.confirm_runtime_quiesced != "YES":
        raise MaintenanceBackupError("复制运行状态必须显式传入 --confirm-runtime-quiesced YES。")
    _assert_service_is_inactive()
    release_root = _release_root()
    settings = load_settings(project_root=release_root)
    config_file = release_root / "configs" / "app.yaml"
    ontology_dir = release_root / "ontology"
    output_parent = _assert_external_output_parent(
        args.output_parent,
        (release_root, settings.reports_dir, settings.storage_dir),
    )
    with tempfile.TemporaryDirectory(prefix=".northstar-backup.", dir=output_parent) as raw_workspace:
        workspace = Path(raw_workspace)
        dump = create_postgresql_dump(settings.database_url, output_path=workspace / "database.dump")
        metadata_dir = _snapshot_release_metadata(release_root, workspace / "release-metadata")
        bundle = create_backup_bundle(
            BackupBundleSources(
                database_dump=dump.path,
                config_file=config_file,
                ontology_dir=ontology_dir,
                reports_dir=settings.reports_dir,
                storage_dir=settings.storage_dir,
                release_metadata_dir=metadata_dir,
            ),
            output_parent=output_parent,
            now=datetime.now(timezone.utc),
            pre_publish_check=_assert_service_is_inactive,
        )
    return {
        "status": "created",
        "bundle_id": bundle.bundle_id,
        "path": str(bundle.path),
        "created_at": bundle.created_at,
        "entry_count": bundle.entry_count,
        "manifest_sha256": bundle.sha256,
    }


def _verify(args: argparse.Namespace) -> dict[str, object]:
    bundle = verify_backup_bundle(args.bundle_dir)
    verify_postgresql_dump(bundle.path / "postgresql" / "database.dump")
    return {
        "status": "verified",
        "bundle_id": bundle.bundle_id,
        "path": str(bundle.path),
        "created_at": bundle.created_at,
        "entry_count": bundle.entry_count,
        "manifest_sha256": bundle.sha256,
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        payload = _create(args) if args.command == "create" else _verify(args)
    except (BackupBundleError, MaintenanceBackupError, PostgreSQLBackupError, ValueError) as exc:
        print(f"备份维护操作失败：{redact_text(str(exc))}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
