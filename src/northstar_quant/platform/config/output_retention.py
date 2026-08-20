"""运行输出清理策略的严格配置加载器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.config.yaml_loader import load_yaml


class OutputRetentionConfigError(ValueError):
    """运行输出清理配置缺失、字段不完整或不安全。"""


_ROOT_FIELDS = frozenset(
    {
        "version",
        "enabled",
        "download_cache_retention_days",
        "temporary_file_retention_days",
    }
)


@dataclass(frozen=True, slots=True)
class OutputRetentionPolicy:
    """仅控制显式运行的下载缓存与安全临时文件清理。"""

    enabled: bool
    download_cache_retention_days: int
    temporary_file_retention_days: int


def get_output_retention_config_path(path: str | Path | None = None) -> Path:
    """返回运行输出清理配置的绝对路径。"""

    if path is None:
        return get_settings().project_root / "configs" / "maintenance" / "output_retention.yaml"
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = get_settings().project_root / candidate
    return candidate.resolve()


def load_output_retention_policy(
    path: str | Path | None = None,
) -> OutputRetentionPolicy:
    """读取策略；拒绝未知字段，避免拼写错误改变删除范围。"""

    config_path = get_output_retention_config_path(path)
    if not config_path.is_file():
        raise OutputRetentionConfigError(f"运行输出清理配置不存在：{config_path}")

    payload = load_yaml(config_path)
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise OutputRetentionConfigError("运行输出清理配置字段不完整或包含未知字段")
    if payload["version"] != 1:
        raise OutputRetentionConfigError("运行输出清理配置 version 当前必须为 1")

    return OutputRetentionPolicy(
        enabled=_required_bool(payload["enabled"], "enabled"),
        download_cache_retention_days=_positive_int(
            payload["download_cache_retention_days"],
            "download_cache_retention_days",
        ),
        temporary_file_retention_days=_positive_int(
            payload["temporary_file_retention_days"],
            "temporary_file_retention_days",
        ),
    )


def _required_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise OutputRetentionConfigError(f"{field} 必须是布尔值")
    return value


def _positive_int(value: Any, field: str) -> int:
    if type(value) is bool or not isinstance(value, int) or value < 1:
        raise OutputRetentionConfigError(f"{field} 必须是大于等于 1 的整数")
    return value
