#!/usr/bin/env python3
"""Run the repository-local uv executable without relying on PATH."""

from __future__ import annotations

import os
import subprocess
import sys

try:  # 支持直接脚本入口与包内导入。
    from .platform_support import PlatformSupportError, require_linux_x86_64
    from .project_tools import (
        PROJECT_ROOT,
        ProjectToolError,
        repository_uv_cache_directory,
        repository_uv_executable,
    )
except ImportError:  # pragma: no cover - 直接脚本入口会走此分支。
    from platform_support import PlatformSupportError, require_linux_x86_64
    from project_tools import (
        PROJECT_ROOT,
        ProjectToolError,
        repository_uv_cache_directory,
        repository_uv_executable,
    )


def main(arguments: list[str] | None = None) -> int:
    try:
        require_linux_x86_64()
        executable = repository_uv_executable()
    except (PlatformSupportError, ProjectToolError) as error:
        print(f"无法启动仓库本地 uv：{error}", file=sys.stderr)
        return 1
    environment = dict(os.environ)
    # 项目命令始终复用仓库自身的缓存，不能被外部 UV_NO_CACHE 悄然禁用。
    environment.pop("UV_NO_CACHE", None)
    environment["UV_CACHE_DIR"] = str(repository_uv_cache_directory())
    try:
        return subprocess.run(
            [str(executable), *(arguments if arguments is not None else sys.argv[1:])],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
        ).returncode
    except OSError as error:
        print(f"无法启动仓库本地 uv：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
