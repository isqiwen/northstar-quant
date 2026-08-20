#!/usr/bin/env python3
"""检查跨平台开发工作站的必要条件。

本脚本只读取本地状态，不会创建 `.env`、启动 Docker、运行迁移或触发任何交易命令。
Windows 和 Linux 都可通过 ``uv run python scripts/dev/check_env.py`` 调用。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import TypedDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMUM_PYTHON = (3, 11)


class CheckResult(TypedDict):
    name: str
    status: str
    message: str


def _result(name: str, status: str, message: str) -> CheckResult:
    return {"name": name, "status": status, "message": message}


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


def _check_docker_compose(*, require_docker: bool) -> CheckResult:
    if not shutil.which("docker"):
        return _result(
            "Docker Compose v2",
            _optional_status(require_docker),
            "未检查：Docker 命令不存在。",
        )
    result = _command_result(["docker", "compose", "version"])
    if result is not None and result.returncode == 0:
        return _result("Docker Compose v2", "ok", "docker compose v2 可用。")
    return _result(
        "Docker Compose v2",
        _optional_status(require_docker),
        "未找到 docker compose v2；本地 PostgreSQL 与集成测试不可用。",
    )


def _local_docker_context() -> tuple[bool, str]:
    """只接受 Unix socket 或 Windows named pipe，避免检查时访问远程 daemon。"""

    if os.getenv("DOCKER_HOST"):
        return False, "检测到 DOCKER_HOST；开发工作站只允许本机 Docker daemon。"
    if not shutil.which("docker"):
        return False, "未检查：Docker 命令不存在。"
    context = _command_result(["docker", "context", "show"])
    if context is None or context.returncode != 0 or not context.stdout.strip():
        return False, "无法确认 Docker context。"
    endpoint = _command_result(
        [
            "docker",
            "context",
            "inspect",
            context.stdout.strip(),
            "--format",
            "{{ .Endpoints.docker.Host }}",
        ]
    )
    host = endpoint.stdout.strip() if endpoint is not None else ""
    if endpoint is None or endpoint.returncode != 0:
        return False, "无法读取 Docker context 终端地址。"
    if host.startswith(("unix://", "npipe:")):
        return True, "本机 Docker context 已确认。"
    return False, "Docker context 不是本机 Unix socket 或 Windows named pipe。"


def _check_docker_daemon(*, require_docker: bool) -> CheckResult:
    is_local, message = _local_docker_context()
    if not is_local:
        return _result(
            "Docker daemon",
            _optional_status(require_docker),
            message,
        )
    result = _command_result(["docker", "info", "--format", "{{.ServerVersion}}"])
    if result is not None and result.returncode == 0:
        return _result("Docker daemon", "ok", "本地 Docker daemon 可达。")
    return _result(
        "Docker daemon",
        _optional_status(require_docker),
        "Docker daemon 不可达；检查 Docker Desktop/服务状态和本地 context。",
    )


def check_environment(
    *,
    require_config: bool,
    require_docker: bool,
    require_just: bool = False,
    require_uv: bool = True,
    require_git: bool = False,
    require_deploy_tools: bool = False,
) -> list[CheckResult]:
    """返回当前工作站的无秘密检查结果。"""

    results: list[CheckResult] = []
    current_system = platform.system()
    if current_system in {"Windows", "Linux"}:
        results.append(_result("操作系统", "ok", f"{current_system}（Tier 1 开发平台）。"))
    else:
        results.append(
            _result(
                "操作系统",
                "warn",
                f"{current_system} 不在 Windows/Linux Tier 1 开发支持范围内。",
            )
        )
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
            _check_command("uv", "uv", required=require_uv),
            _check_command("just", "just", required=require_just),
            _check_command("git", "Git", required=require_git),
            _check_command("docker", "Docker", required=require_docker),
            _check_docker_compose(require_docker=require_docker),
            _check_docker_daemon(require_docker=require_docker),
            _check_command("ssh", "SSH", required=require_deploy_tools),
            _check_command("scp", "SCP", required=require_deploy_tools),
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
                    "文件不存在；请运行 just dev-setup，或直接运行 "
                    "python scripts/dev/setup.py --initialize-config。",
                )
            )
        else:
            results.append(_result(label, "warn", "文件不存在；首次初始化时会显式创建。"))

    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查 Northstar Quant 跨平台开发环境。")
    parser.add_argument(
        "--require-config",
        action="store_true",
        help="将缺少 .env 或 configs/app.yaml 视为错误。",
    )
    parser.add_argument(
        "--require-docker",
        action="store_true",
        help="将缺少 Docker 视为错误。",
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
        help="将缺少 ssh/scp 部署控制面工具视为错误。",
    )
    parser.add_argument("--json", action="store_true", help="输出稳定 JSON。")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    results = check_environment(
        require_config=args.require_config,
        require_docker=args.require_docker,
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
