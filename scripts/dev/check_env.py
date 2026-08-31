#!/usr/bin/env python3
"""检查 Linux x86_64 开发工作站的必要条件。

本脚本只读取本地状态，不会创建 `.env`、启动或停止 PostgreSQL、运行迁移或触发任何交易命令。
该只读检查不需要项目依赖；非 Linux x86_64 主机明确失败关闭。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import NotRequired, TypedDict

try:  # 支持直接脚本入口与包内导入。
    from .project_tools import (
        ProjectToolError,
        repository_just_executable,
        repository_uv_executable,
    )
    from .platform_support import PlatformSupportError, require_linux_x86_64
except ImportError:  # pragma: no cover - 直接脚本入口会走此分支。
    from project_tools import ProjectToolError, repository_just_executable, repository_uv_executable
    from platform_support import PlatformSupportError, require_linux_x86_64


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMUM_PYTHON = (3, 11)
LOCAL_POSTGRES_HOST = "127.0.0.1"
DEFAULT_POSTGRES_PORT = 5432


class CheckResult(TypedDict):
    name: str
    status: str
    message: str
    code: NotRequired[str]


def _result(
    name: str,
    status: str,
    message: str,
    *,
    code: str | None = None,
) -> CheckResult:
    result: CheckResult = {"name": name, "status": status, "message": message}
    if code is not None:
        result["code"] = code
    return result


def _optional_status(required: bool) -> str:
    return "error" if required else "warn"


def _command_result(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    """以只读方式查询外部工具；超时或失败由调用方解释。"""

    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _check_command(command: str, label: str, *, required: bool) -> CheckResult:
    if shutil.which(command):
        return _result(label, "ok", "已找到可执行命令。")
    status = _optional_status(required)
    message = "未找到可执行命令。" if required else "未安装；对应可选工作流不可用。"
    return _result(label, status, message)


def _check_repository_uv(*, required: bool) -> CheckResult:
    try:
        executable = repository_uv_executable()
    except (PlatformSupportError, ProjectToolError) as error:
        return _result("uv", _optional_status(required), str(error))
    return _result("uv", "ok", f"已找到仓库本地可执行文件：{executable}。")


def _check_repository_just(*, required: bool) -> CheckResult:
    try:
        executable = repository_just_executable()
    except (PlatformSupportError, ProjectToolError) as error:
        return _result("just", _optional_status(required), str(error))
    return _result("just", "ok", f"已找到仓库本地可执行文件：{executable}。")


def _configured_postgres_port() -> int | None:
    """读取本地活动配置中的端口；不读取或输出任何秘密字段。"""

    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return DEFAULT_POSTGRES_PORT
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != "POSTGRES_PORT":
                continue
            port = int(value.strip().strip("\"'"))
            return port if 1 <= port <= 65535 else None
    except (OSError, UnicodeError, ValueError):
        return None
    return DEFAULT_POSTGRES_PORT


def _check_local_postgres_service(*, required: bool) -> CheckResult:
    """确认已运行的 native PostgreSQL 只在固定 loopback 目标上可达。"""

    port = _configured_postgres_port()
    if port is None:
        return _result(
            "本机 PostgreSQL",
            _optional_status(required),
            ".env 中的 POSTGRES_PORT 无效；已拒绝猜测服务目标。",
        )
    if not shutil.which("pg_isready"):
        return _result(
            "本机 PostgreSQL",
            _optional_status(required),
            "未检查：缺少 pg_isready。",
        )
    result = _command_result(
        [
            "pg_isready",
            "--host",
            LOCAL_POSTGRES_HOST,
            "--port",
            str(port),
            "--dbname",
            "postgres",
        ]
    )
    if result is not None and result.returncode == 0:
        return _result(
            "本机 PostgreSQL",
            "ok",
            f"已运行并可通过 {LOCAL_POSTGRES_HOST}:{port} 访问。",
        )
    return _result(
        "本机 PostgreSQL",
        _optional_status(required),
        f"无法通过 {LOCAL_POSTGRES_HOST}:{port} 访问；请由操作者启动本机 PostgreSQL 服务后重试。",
    )


def check_environment(
    *,
    require_config: bool,
    require_postgres: bool = False,
    require_just: bool = False,
    require_uv: bool = True,
    require_git: bool = False,
    require_deploy_tools: bool = False,
) -> list[CheckResult]:
    """返回当前工作站的无秘密检查结果。"""

    results: list[CheckResult] = []
    current_system = platform.system()
    current_machine = platform.machine()
    try:
        require_linux_x86_64(system_name=current_system, machine=current_machine)
    except PlatformSupportError as error:
        results.append(_result("操作系统", "error", str(error)))
    else:
        results.append(_result("操作系统", "ok", "Linux x86_64（唯一受支持开发平台）。"))
    python_version = sys.version_info[:2]
    if python_version >= MINIMUM_PYTHON:
        results.append(
            _result("Python", "ok", f"Python {python_version[0]}.{python_version[1]}")
        )
    else:
        results.append(
            _result(
                "Python",
                "error",
                f"需要 Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} 或更高版本。",
            )
        )

    results.extend(
        (
            _check_repository_uv(required=require_uv),
            _check_repository_just(required=require_just),
            _check_command("git", "Git", required=require_git),
            _check_command("pg_isready", "pg_isready", required=require_postgres),
            _check_command("psql", "psql", required=require_postgres),
            _check_command("createdb", "createdb", required=require_postgres),
            _check_command("pg_dump", "pg_dump", required=require_postgres),
            _check_command("pg_restore", "pg_restore", required=require_postgres),
            _check_local_postgres_service(required=require_postgres),
            _check_command("ssh", "SSH", required=require_deploy_tools),
            _check_command("ssh-keygen", "OpenSSH ssh-keygen", required=require_deploy_tools),
        )
    )

    for path, label in (
        (PROJECT_ROOT / ".env", "活动环境文件 .env"),
        (PROJECT_ROOT / "configs" / "app.yaml", "活动应用配置 configs/app.yaml"),
    ):
        if path.is_file():
            results.append(_result(label, "ok", "文件存在。"))
        elif require_config:
            results.append(
                _result(
                    label,
                    "error",
                    "文件不存在；请运行 python scripts/dev/setup.py --initialize-workstation，或在已完成依赖同步后直接运行 "
                    "python scripts/dev/setup.py --initialize-config。",
                )
            )
        else:
            results.append(_result(label, "warn", "文件不存在；首次初始化时会显式创建。"))

    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查 Northstar Quant Linux x86_64 开发环境。")
    parser.add_argument(
        "--require-config",
        action="store_true",
        help="将缺少 .env 或 configs/app.yaml 视为错误。",
    )
    parser.add_argument(
        "--require-postgres",
        action="store_true",
        help="将缺少原生 PostgreSQL 客户端或本机 loopback 服务视为错误。",
    )
    parser.add_argument(
        "--require-just",
        action="store_true",
        help="将缺少 just 视为错误。",
    )
    parser.add_argument("--require-git", action="store_true", help="将缺少 Git 视为错误。")
    parser.add_argument(
        "--require-deploy-tools",
        action="store_true",
        help="将缺少 ssh/ssh-keygen 部署控制面工具视为错误。",
    )
    parser.add_argument("--json", action="store_true", help="输出稳定 JSON。")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    results = check_environment(
        require_config=args.require_config,
        require_postgres=args.require_postgres,
        require_just=args.require_just,
        require_git=args.require_git,
        require_deploy_tools=args.require_deploy_tools,
    )
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for item in results:
            prefix = {"ok": "通过", "warn": "提示", "error": "失败"}[item["status"]]
            print(f"[{prefix}] {item['name']}：{item['message']}")

    return 1 if any(item["status"] == "error" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
