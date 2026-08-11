"""应用运行输出路径的严格 YAML 配置。

运行输出位置不是秘密，统一由 ``configs/app.yaml`` 的 ``runtime`` 段管理。
开发者可以创建未跟踪的 ``configs/app.local.yaml`` 覆盖整段配置；该文件必须
只包含完整的 ``runtime`` 段，避免局部覆盖造成部署路径难以审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.config.yaml_loader import load_yaml


class AppRuntimeConfigError(ValueError):
    """应用运行输出路径配置不完整、不明确或不安全。"""


_RUNTIME_FIELDS = frozenset({"storage_dir", "downloads_dir", "reports_dir", "log_dir"})
_LOCAL_ROOT_FIELDS = frozenset({"runtime"})


@dataclass(frozen=True, slots=True)
class AppRuntimePaths:
    """解析后的运行输出根目录。"""

    storage_dir: Path
    downloads_dir: Path | None
    reports_dir: Path
    log_dir: Path


def get_app_config_path(project_root: str | Path) -> Path:
    """返回基础应用配置文件路径。"""

    return _resolve_project_root(project_root) / "configs" / "app.yaml"


def get_app_local_config_path(project_root: str | Path) -> Path:
    """返回未跟踪的本机运行输出覆盖文件路径。"""

    return _resolve_project_root(project_root) / "configs" / "app.local.yaml"


def load_app_runtime_paths(project_root: str | Path) -> AppRuntimePaths:
    """从基础配置及可选本机覆盖文件加载运行输出路径。"""

    root = _resolve_project_root(project_root)
    base_path = get_app_config_path(root)
    base_payload = _load_mapping(base_path, "基础应用配置")
    runtime_payload = _parse_runtime_block(base_payload.get("runtime"), base_path)

    local_path = get_app_local_config_path(root)
    if local_path.is_symlink() and not local_path.exists():
        raise AppRuntimeConfigError(
            f"本机应用覆盖配置是断开的符号链接：{local_path}"
        )
    if local_path.exists():
        local_payload = _load_mapping(local_path, "本机应用覆盖配置")
        if set(local_payload) != _LOCAL_ROOT_FIELDS:
            raise AppRuntimeConfigError(
                "configs/app.local.yaml 只能包含完整的 runtime 配置段"
            )
        runtime_payload = _parse_runtime_block(local_payload.get("runtime"), local_path)

    storage_dir = runtime_payload["storage_dir"]
    downloads_dir = runtime_payload["downloads_dir"]
    reports_dir = runtime_payload["reports_dir"]
    log_dir = runtime_payload["log_dir"]
    assert storage_dir is not None
    assert reports_dir is not None
    assert log_dir is not None

    return AppRuntimePaths(
        storage_dir=_resolve_runtime_path(storage_dir, root, "storage_dir"),
        downloads_dir=(
            None
            if downloads_dir is None
            else _resolve_runtime_path(downloads_dir, root, "downloads_dir")
        ),
        reports_dir=_resolve_runtime_path(reports_dir, root, "reports_dir"),
        log_dir=_resolve_runtime_path(log_dir, root, "log_dir"),
    )


def _resolve_project_root(project_root: str | Path) -> Path:
    root = Path(project_root)
    if not root.is_absolute():
        root = root.resolve()
    return root.resolve()


def _load_mapping(path: Path, description: str) -> dict[str, Any]:
    if not path.is_file():
        raise AppRuntimeConfigError(f"{description}不存在：{path}")
    payload = load_yaml(path)
    if not isinstance(payload, dict):
        raise AppRuntimeConfigError(f"{description}必须是 YAML 对象：{path}")
    return payload


def _parse_runtime_block(value: Any, path: Path) -> dict[str, str | None]:
    if not isinstance(value, dict) or set(value) != _RUNTIME_FIELDS:
        raise AppRuntimeConfigError(
            f"{path} 的 runtime 必须完整包含且只包含："
            "storage_dir、downloads_dir、reports_dir、log_dir"
        )

    return {
        "storage_dir": _required_path_text(value["storage_dir"], "storage_dir", path),
        "downloads_dir": _optional_path_text(value["downloads_dir"], "downloads_dir", path),
        "reports_dir": _required_path_text(value["reports_dir"], "reports_dir", path),
        "log_dir": _required_path_text(value["log_dir"], "log_dir", path),
    }


def _required_path_text(value: Any, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppRuntimeConfigError(f"{path} 的 runtime.{field} 必须是非空路径字符串")
    return value.strip()


def _optional_path_text(value: Any, field: str, path: Path) -> str | None:
    if value is None:
        return None
    return _required_path_text(value, field, path)


def _resolve_runtime_path(value: str, project_root: Path, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    resolved = path.resolve()
    if resolved == project_root:
        raise AppRuntimeConfigError(f"runtime.{field} 不能指向项目根目录")
    return resolved
