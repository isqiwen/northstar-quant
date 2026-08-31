#!/usr/bin/env python3
"""Run the repository-local just executable without relying on PATH."""

from __future__ import annotations

import subprocess
import sys

try:  # 支持直接脚本入口与包内导入。
    from .platform_support import PlatformSupportError, require_linux_x86_64
    from .project_tools import PROJECT_ROOT, ProjectToolError, repository_just_executable
except ImportError:  # pragma: no cover - 直接脚本入口会走此分支。
    from platform_support import PlatformSupportError, require_linux_x86_64
    from project_tools import PROJECT_ROOT, ProjectToolError, repository_just_executable


def main(arguments: list[str] | None = None) -> int:
    try:
        require_linux_x86_64()
        executable = repository_just_executable()
    except (PlatformSupportError, ProjectToolError) as error:
        print(f"无法启动仓库本地 just：{error}", file=sys.stderr)
        return 1
    try:
        return subprocess.run(
            [str(executable), *(arguments if arguments is not None else sys.argv[1:])],
            cwd=PROJECT_ROOT,
            check=False,
        ).returncode
    except OSError as error:
        print(f"无法启动仓库本地 just：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
