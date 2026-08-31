"""Resolve untracked, repository-local development tools."""

from __future__ import annotations

import os
from pathlib import Path
import stat

try:  # 支持直接脚本入口与包内导入。
    from .platform_support import require_linux_x86_64
except ImportError:  # pragma: no cover - 直接脚本入口会走此分支。
    from platform_support import require_linux_x86_64


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_DIRECTORY_NAME = ".northstar"


class ProjectToolError(RuntimeError):
    """Raised when a required repository-local tool is unavailable or unsafe."""


def repository_tool_root(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the untracked tool root without creating it."""

    return project_root.resolve(strict=False) / TOOL_DIRECTORY_NAME


def repository_uv_cache_directory(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the repository-owned uv cache location without creating it."""

    return repository_tool_root(project_root=project_root) / "cache" / "uv"


def _repository_tool_executable(
    tool_name: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    """Return one verified launcher below ``.northstar/bin``, never a PATH fallback."""

    require_linux_x86_64()
    tool_root = repository_tool_root(project_root=project_root)
    try:
        metadata = tool_root.lstat()
    except FileNotFoundError:
        raise ProjectToolError(f"未找到仓库本地 {tool_name}；请先运行开发初始化。") from None
    except OSError as error:
        raise ProjectToolError(f"无法检查仓库工具目录：{error}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ProjectToolError("仓库工具目录不能是符号链接。")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProjectToolError("仓库工具目录必须是目录。")

    resolved_root = tool_root.resolve(strict=True)
    candidate = tool_root / "bin" / tool_name
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise ProjectToolError(f"未找到仓库本地 {tool_name}；请先运行开发初始化。") from None
    except OSError as error:
        raise ProjectToolError(f"无法解析仓库本地 {tool_name}：{error}") from error
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ProjectToolError(f"仓库本地 {tool_name} 不能指向 .northstar 外部。") from error
    try:
        executable_metadata = resolved.stat()
    except OSError as error:
        raise ProjectToolError(f"无法检查仓库本地 {tool_name}：{error}") from error
    if not stat.S_ISREG(executable_metadata.st_mode):
        raise ProjectToolError(f"仓库本地 {tool_name} 必须是普通文件。")
    if not os.access(resolved, os.X_OK):
        raise ProjectToolError(f"仓库本地 {tool_name} 不可执行。")
    return candidate


def repository_uv_executable(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the verified project-local uv launcher, never a PATH fallback."""

    return _repository_tool_executable("uv", project_root=project_root)


def repository_just_executable(*, project_root: Path = PROJECT_ROOT) -> Path:
    """Return the verified project-local just launcher, never a PATH fallback."""

    return _repository_tool_executable("just", project_root=project_root)
