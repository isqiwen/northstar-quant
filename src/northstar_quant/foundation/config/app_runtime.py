"""应用活动 YAML 配置的严格加载器。

``configs/app.example.yaml`` 只用于展示完整的非秘密配置，运行时绝不读取它。
开发与生产环境都只读取同构的 ``configs/app.yaml``；数据库凭据、令牌等秘密
仍由 ``.env`` 或操作系统环境变量提供。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from northstar_quant.foundation.config.yaml_loader import load_yaml


class AppConfigError(ValueError):
    """活动应用配置缺失、结构不完整或包含不安全的值。"""


_APP_ROOT_FIELDS = frozenset({"runtime", "logging"})
_RUNTIME_FIELDS = frozenset({"storage_dir", "downloads_dir", "reports_dir", "log_dir"})
_LOGGING_FIELDS = frozenset(
    {
        "level",
        "console_enabled",
        "file_enabled",
        "filename",
        "when",
        "interval",
        "backup_count",
        "encoding",
        "format",
    }
)
_ALLOWED_LOG_LEVELS = frozenset({"NOTSET", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_WEEKDAY_ROTATION_PATTERN = re.compile(r"W[0-6]")


@dataclass(frozen=True, slots=True)
class AppRuntimePaths:
    """解析后的运行输出根目录。"""

    storage_dir: Path
    downloads_dir: Path | None
    reports_dir: Path
    log_dir: Path


@dataclass(frozen=True, slots=True)
class AppLoggingConfig:
    """经过类型和边界校验后的日志规则。"""

    level: str
    console_enabled: bool
    file_enabled: bool
    filename: str
    when: str
    interval: int
    backup_count: int
    encoding: str
    format: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    """唯一活动应用配置的完整、已校验表示。"""

    runtime: AppRuntimePaths
    logging: AppLoggingConfig


def get_app_config_path(project_root: str | Path) -> Path:
    """返回唯一活动应用配置文件的路径。"""

    return _resolve_project_root(project_root) / "configs" / "app.yaml"


def load_app_config(project_root: str | Path) -> AppConfig:
    """严格读取唯一活动配置 ``configs/app.yaml``。

    示例文件不能充当回退配置，避免某台机器在漏配时以示例值继续运行。
    ``app.local.yaml`` 已彻底废弃；只要发现它就停止启动并要求显式迁移。
    """

    root = _resolve_project_root(project_root)
    _reject_legacy_local_config(root)
    path = get_app_config_path(root)
    payload = _load_active_mapping(path)

    if set(payload) != _APP_ROOT_FIELDS:
        raise AppConfigError(
            f"{path} 必须完整包含且只包含顶层配置段：runtime、logging。"
        )

    return AppConfig(
        runtime=_parse_runtime_block(payload["runtime"], path, root),
        logging=_parse_logging_block(payload["logging"], path),
    )


def load_app_runtime_paths(project_root: str | Path) -> AppRuntimePaths:
    """返回活动应用配置中的运行输出路径。"""

    return load_app_config(project_root).runtime


def _resolve_project_root(project_root: str | Path) -> Path:
    root = Path(project_root)
    if not root.is_absolute():
        root = root.resolve()
    return root.resolve()


def _reject_legacy_local_config(project_root: Path) -> None:
    legacy_path = project_root / "configs" / "app.local.yaml"
    if legacy_path.exists() or legacy_path.is_symlink():
        raise AppConfigError(
            "检测到已废弃的 configs/app.local.yaml。当前版本只读取完整的 "
            "configs/app.yaml；请将其中的值完整迁移到 app.yaml 后删除该文件："
            f"{legacy_path}"
        )


def _load_active_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AppConfigError(
            f"活动应用配置不存在：{path}。请复制 configs/app.example.yaml 为 "
            "configs/app.yaml，并完整填写非秘密配置。"
        )

    try:
        payload = load_yaml(path)
    except OSError as exc:
        raise AppConfigError(f"无法读取活动应用配置：{path}") from exc

    if not isinstance(payload, dict):
        raise AppConfigError(f"活动应用配置必须是 YAML 对象：{path}")
    return payload


def _parse_runtime_block(value: Any, path: Path, project_root: Path) -> AppRuntimePaths:
    if not isinstance(value, dict) or set(value) != _RUNTIME_FIELDS:
        raise AppConfigError(
            f"{path} 的 runtime 必须完整包含且只包含："
            "storage_dir、downloads_dir、reports_dir、log_dir。"
        )

    storage_dir = _required_path_text(value["storage_dir"], "storage_dir", path)
    downloads_dir = _optional_path_text(value["downloads_dir"], "downloads_dir", path)
    reports_dir = _required_path_text(value["reports_dir"], "reports_dir", path)
    log_dir = _required_path_text(value["log_dir"], "log_dir", path)

    return AppRuntimePaths(
        storage_dir=_resolve_runtime_path(storage_dir, project_root, "storage_dir"),
        downloads_dir=(
            None
            if downloads_dir is None
            else _resolve_runtime_path(downloads_dir, project_root, "downloads_dir")
        ),
        reports_dir=_resolve_runtime_path(reports_dir, project_root, "reports_dir"),
        log_dir=_resolve_runtime_path(log_dir, project_root, "log_dir"),
    )


def _parse_logging_block(value: Any, path: Path) -> AppLoggingConfig:
    if not isinstance(value, dict) or set(value) != _LOGGING_FIELDS:
        raise AppConfigError(
            f"{path} 的 logging 必须完整包含且只包含："
            "level、console_enabled、file_enabled、filename、when、interval、"
            "backup_count、encoding、format。"
        )

    return AppLoggingConfig(
        level=_parse_log_level(value["level"], path),
        console_enabled=_required_bool(value["console_enabled"], "console_enabled", path),
        file_enabled=_required_bool(value["file_enabled"], "file_enabled", path),
        filename=_parse_log_filename(value["filename"], path),
        when=_parse_rotation_when(value["when"], path),
        interval=_required_int(value["interval"], "interval", path, minimum=1),
        backup_count=_required_int(value["backup_count"], "backup_count", path, minimum=0),
        encoding=_parse_encoding(value["encoding"], path),
        format=_required_text(value["format"], "format", path, section="logging"),
    )


def _required_path_text(value: Any, field: str, path: Path) -> str:
    return _required_text(value, field, path, section="runtime")


def _optional_path_text(value: Any, field: str, path: Path) -> str | None:
    if value is None:
        return None
    return _required_path_text(value, field, path)


def _required_text(value: Any, field: str, path: Path, *, section: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppConfigError(f"{path} 的 {section}.{field} 必须是非空字符串。")
    return value.strip()


def _required_bool(value: Any, field: str, path: Path) -> bool:
    if type(value) is not bool:
        raise AppConfigError(f"{path} 的 logging.{field} 必须是布尔值。")
    return value


def _required_int(value: Any, field: str, path: Path, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AppConfigError(f"{path} 的 logging.{field} 必须是不小于 {minimum} 的整数。")
    return value


def _parse_log_level(value: Any, path: Path) -> str:
    normalized = _required_text(value, "level", path, section="logging").upper()
    if normalized not in _ALLOWED_LOG_LEVELS:
        allowed = "、".join(sorted(_ALLOWED_LOG_LEVELS))
        raise AppConfigError(f"{path} 的 logging.level 必须是以下值之一：{allowed}。")
    return normalized


def _parse_log_filename(value: Any, path: Path) -> str:
    filename = _required_text(value, "filename", path, section="logging")
    if filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise AppConfigError(
            f"{path} 的 logging.filename 必须是单个文件名，不能包含目录或路径穿越。"
        )
    return filename


def _parse_rotation_when(value: Any, path: Path) -> str:
    when = _required_text(value, "when", path, section="logging")
    if when.lower() == "midnight":
        return "midnight"

    normalized = when.upper()
    if normalized in {"S", "M", "H", "D"} or _WEEKDAY_ROTATION_PATTERN.fullmatch(normalized):
        return normalized
    raise AppConfigError(
        f"{path} 的 logging.when 必须是 S、M、H、D、midnight 或 W0 至 W6。"
    )


def _parse_encoding(value: Any, path: Path) -> str:
    encoding = _required_text(value, "encoding", path, section="logging")
    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        raise AppConfigError(f"{path} 的 logging.encoding 不是可用编码：{encoding}。") from exc
    return encoding


def _resolve_runtime_path(value: str, project_root: Path, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    resolved = path.resolve()
    if resolved == project_root:
        raise AppConfigError(f"runtime.{field} 不能指向项目根目录。")
    return resolved
