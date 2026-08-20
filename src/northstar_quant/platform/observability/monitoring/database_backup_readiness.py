"""只读评估 PostgreSQL 备份与恢复演练证据。

本模块故意不执行 pg_dump、pg_restore、对象存储访问或写入操作。第一阶段只能确认
证据的结构、时效和内部关联，不能独立证明外部介质和恢复结果，因此永远不会返回
``pass``。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Literal
from uuid import UUID

from northstar_quant.platform.config.database_backup_readiness import (
    DatabaseBackupReadinessPolicy,
)


ReadinessStatus = Literal["skipped", "warn", "fail"]
_MAX_EVIDENCE_BYTES = 64 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_ROOT_FIELDS = frozenset({"version", "backup", "restore_drill"})
_BACKUP_FIELDS = frozenset(
    {"completed_at", "artifact_id", "artifact_sha256", "artifact_size_bytes"}
)
_RESTORE_FIELDS = frozenset({"completed_at", "artifact_id", "status", "method"})


@dataclass(frozen=True, slots=True)
class DatabaseBackupReadiness:
    """供健康检查和命令行复用的只读证据评估结果。"""

    status: ReadinessStatus
    message: str
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        """将结果转换为稳定、无秘密的 JSON 数据。"""

        return asdict(self)


def evaluate_database_backup_readiness(
    policy: DatabaseBackupReadinessPolicy,
    *,
    storage_dir: str | Path,
    now: datetime | None = None,
) -> DatabaseBackupReadiness:
    """验证证据，不创建目录、不读取目录外内容，也不调用任何外部系统。"""

    if not policy.enabled:
        return DatabaseBackupReadiness(
            status="skipped",
            message="PostgreSQL 备份/恢复证据策略未启用；这不代表系统已备份或可恢复。",
            details={"policy_enabled": False},
        )

    root = Path(storage_dir).resolve()
    evidence_path = root.joinpath(*policy.evidence_relative_path.parts)
    if evidence_path.is_symlink():
        return _fail("证据文件是符号链接，已拒绝读取。", evidence_path=evidence_path)
    try:
        resolved_evidence_path = evidence_path.resolve(strict=False)
        resolved_evidence_path.relative_to(root)
    except ValueError:
        return _fail("证据路径越出 runtime.storage_dir，已拒绝读取。")
    if not evidence_path.is_file():
        return _fail("未找到 PostgreSQL 备份/恢复就绪证据文件。", evidence_path=evidence_path)
    try:
        evidence_size = evidence_path.stat().st_size
    except OSError:
        return _fail("无法读取 PostgreSQL 备份/恢复就绪证据文件。", evidence_path=evidence_path)
    if evidence_size > _MAX_EVIDENCE_BYTES:
        return _fail(
            "PostgreSQL 备份/恢复就绪证据文件超过允许大小，已拒绝读取。",
            evidence_path=evidence_path,
        )
    try:
        raw_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _fail("PostgreSQL 备份/恢复就绪证据不是有效 UTF-8 JSON。", evidence_path=evidence_path)

    try:
        backup_completed_at, restore_completed_at, artifact_id = _validate_evidence(
            raw_payload,
            now=_normalize_now(now),
            policy=policy,
        )
    except ValueError as exc:
        return _fail(str(exc), evidence_path=evidence_path)

    return DatabaseBackupReadiness(
        status="warn",
        message=(
            "已读取时效内的 PostgreSQL 备份与隔离恢复演练证据；"
            "项目尚未独立核验外部备份介质或恢复目标，因此不能标记为 pass。"
        ),
        details={
            "policy_enabled": True,
            "evidence_path": str(evidence_path),
            "backup_completed_at": backup_completed_at.isoformat().replace("+00:00", "Z"),
            "restore_drill_completed_at": restore_completed_at.isoformat().replace("+00:00", "Z"),
            "artifact_id": artifact_id,
        },
    )


def _validate_evidence(
    payload: Any,
    *,
    now: datetime,
    policy: DatabaseBackupReadinessPolicy,
) -> tuple[datetime, datetime, str]:
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise ValueError("备份/恢复证据必须完整包含且只包含 version、backup、restore_drill。")
    if payload["version"] != 1:
        raise ValueError("备份/恢复证据 version 当前必须为 1。")

    backup = payload["backup"]
    restore_drill = payload["restore_drill"]
    if not isinstance(backup, dict) or set(backup) != _BACKUP_FIELDS:
        raise ValueError("备份/恢复证据的 backup 字段不完整或包含未知字段。")
    if not isinstance(restore_drill, dict) or set(restore_drill) != _RESTORE_FIELDS:
        raise ValueError("备份/恢复证据的 restore_drill 字段不完整或包含未知字段。")

    backup_completed_at = _utc_timestamp(backup["completed_at"], "backup.completed_at")
    restore_completed_at = _utc_timestamp(
        restore_drill["completed_at"],
        "restore_drill.completed_at",
    )
    artifact_id = _artifact_id(backup["artifact_id"], "backup.artifact_id")
    restore_artifact_id = _artifact_id(
        restore_drill["artifact_id"],
        "restore_drill.artifact_id",
    )
    if restore_artifact_id != artifact_id:
        raise ValueError("隔离恢复演练引用的 artifact_id 与备份证据不一致。")
    _sha256(backup["artifact_sha256"])
    _positive_size(backup["artifact_size_bytes"])
    if restore_drill["status"] != "passed":
        raise ValueError("隔离恢复演练状态不是 passed。")
    if not isinstance(restore_drill["method"], str) or not restore_drill["method"].strip():
        raise ValueError("restore_drill.method 必须是非空字符串。")

    if backup_completed_at > now or restore_completed_at > now:
        raise ValueError("备份或恢复演练时间不能晚于当前 UTC 时间。")
    if restore_completed_at < backup_completed_at:
        raise ValueError("隔离恢复演练时间不能早于对应备份完成时间。")
    backup_age_hours = (now - backup_completed_at).total_seconds() / 3600
    if backup_age_hours > policy.max_backup_age_hours:
        raise ValueError("PostgreSQL 备份证据已超过策略允许时效。")
    restore_age_days = (now - restore_completed_at).total_seconds() / 86_400
    if restore_age_days > policy.max_restore_verification_age_days:
        raise ValueError("PostgreSQL 隔离恢复演练证据已超过策略允许时效。")
    return backup_completed_at, restore_completed_at, artifact_id


def _utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} 必须是以 Z 结尾的 UTC ISO-8601 时间。")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} 不是有效 UTC ISO-8601 时间。") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{field} 必须使用 UTC 时区。")
    return parsed


def _artifact_id(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是 UUID 字符串。")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 UUID 字符串。") from exc
    return str(parsed)


def _sha256(value: Any) -> None:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ValueError("backup.artifact_sha256 必须是 64 位十六进制 SHA-256。")


def _positive_size(value: Any) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("backup.artifact_size_bytes 必须是大于 0 的整数。")


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now 必须包含时区。")
    return now.astimezone(timezone.utc)


def _fail(message: str, *, evidence_path: Path | None = None) -> DatabaseBackupReadiness:
    details: dict[str, object] = {"policy_enabled": True}
    if evidence_path is not None:
        details["evidence_path"] = str(evidence_path)
    return DatabaseBackupReadiness(status="fail", message=message, details=details)
