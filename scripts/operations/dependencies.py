"""Check externally provisioned tools before preparing application directories."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


def run(*args: str) -> None:
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL)


def available(*args: str) -> bool:
    try:
        return (
            subprocess.run(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def admin(*args: str) -> None:
    elevation = [] if os.geteuid() == 0 else ["sudo", *([] if sys.stdin.isatty() else ["-n"]), "--"]
    run(*elevation, *args)


def prepare(request: dict) -> None:
    os.environ["PATH"] += os.pathsep + str(Path.home() / ".local/bin") + ":/usr/sbin:/sbin"
    for command in (
        ("git", "--version"),
        ("uv", "--version"),
        ("docker", "compose", "version"),
        ("docker", "buildx", "version"),
    ):
        if not available(*command):
            raise ValueError(f"请在主机预先安装部署依赖：{' '.join(command)}")
    if not available("docker", "info"):
        raise ValueError("Docker 不可访问，请预先启动服务并授予 northstar 使用权限")
    admin(
        sys.executable,
        "-c",
        request["directory_program"],
        json.dumps({"app": request["app"], "uid": os.getuid(), "gid": os.getgid()}),
    )
    private = Path(request["env_file"])
    if private.exists() and (not private.is_file() or stat.S_IMODE(private.stat().st_mode) & 0o077):
        raise ValueError("已有运行配置必须为私有普通文件（chmod 600）")
    print("部署依赖检查通过", flush=True)


if __name__ == "__main__":
    try:
        prepare(json.loads(sys.argv[1]))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"部署前检查失败：{error}")
