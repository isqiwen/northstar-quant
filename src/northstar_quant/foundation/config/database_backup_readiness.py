"""PostgreSQL 备份/恢复就绪证据策略的严格加载器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any

from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.config.yaml_loader import load_yaml


class DatabaseBackupReadinessConfigError(ValueError):
    """备份就绪策略缺失、字段不完整或包含不安全值。"""


_ROOT_FIELDS = frozenset(
    {
        "version",
        "enabled",
        "evidence_relative_path",
        "max_backup_age_hours",
        "max_restore_verification_age_days",
    }
)
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True, slots=True)
class DatabaseBackupReadinessPolicy:
    """只读的 PostgreSQL 备份和隔离恢复演练证据要求。"""

    enabled: bool
    evidence_relative_path: PurePosixPath
    max_backup_age_hours: int
    max_restore_verification_age_days: int


def get_database_backup_readiness_config_path(path: str | Path | None = None) -> Path:
    """返回策略文件的绝对路径。"""

    if path is None:
        return (
            get_settings().project_root
            / "configs"
            / "maintenance"
            / "database_backup_readiness.yaml"
        )
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = get_settings().project_root / candidate
    return candidate.resolve()


def load_database_backup_readiness_policy(
    path: str | Path | None = None,
) -> DatabaseBackupReadinessPolicy:
    """读取完整策略；拒绝未知字段，避免拼写错误降低恢复要求。"""

    config_path = get_database_backup_readiness_config_path(path)
    if not config_path.is_file():
        raise DatabaseBackupReadinessConfigError(
            f"PostgreSQL 备份就绪策略不存在：{config_path}"
        )

    payload = load_yaml(config_path)
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise DatabaseBackupReadinessConfigError(
            "PostgreSQL 备份就绪策略字段不完整或包含未知字段"
        )
    if payload["version"] != 1:
        raise DatabaseBackupReadinessConfigError(
            "PostgreSQL 备份就绪策略 version 当前必须为 1"
        )

    return DatabaseBackupReadinessPolicy(
        enabled=_required_bool(payload["enabled"], "enabled"),
        evidence_relative_path=_safe_relative_path(payload["evidence_relative_path"]),
        max_backup_age_hours=_positive_int(
            payload["max_backup_age_hours"],
            "max_backup_age_hours",
        ),
        max_restore_verification_age_days=_positive_int(
            payload["max_restore_verification_age_days"],
            "max_restore_verification_age_days",
        ),
    )


def _required_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise DatabaseBackupReadinessConfigError(f"{field} 必须是布尔值")
    return value


def _positive_int(value: Any, field: str) -> int:
    if type(value) is bool or not isinstance(value, int) or value < 1:
        raise DatabaseBackupReadinessConfigError(f"{field} 必须是大于等于 1 的整数")
    return value


def _safe_relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise DatabaseBackupReadinessConfigError(
            "evidence_relative_path 必须是非空的相对 POSIX 路径"
        )
    normalized = value.strip()
    if "\\" in normalized or _WINDOWS_DRIVE_PATTERN.match(normalized):
        raise DatabaseBackupReadinessConfigError(
            "evidence_relative_path 必须使用相对 POSIX 路径，不能包含 Windows 驱动器或反斜杠"
        )
    path = PurePosixPath(normalized)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise DatabaseBackupReadinessConfigError(
            "evidence_relative_path 必须位于 runtime.storage_dir 内，不能使用绝对路径、. 或 .."
        )
    if path.suffix != ".json":
        raise DatabaseBackupReadinessConfigError(
            "evidence_relative_path 必须指向 .json 证据文件"
        )
    return path
