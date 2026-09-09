#!/usr/bin/env python3
"""Manage one Northstar application via SSH; credentials remain on its host."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = ("database", "data-hub", "research", "live")


def configuration(path: Path, app: str) -> dict:
    item = tomllib.loads(path.read_text()).get(app.replace("-", "_"), {})
    for key in ("host", "user"):
        if not isinstance(item.get(key), str) or not re.fullmatch(
            r"[a-zA-Z0-9_][a-zA-Z0-9_.:-]*", item[key]
        ):
            raise ValueError(f"{app}.{key} 必须填写有效的 SSH 地址/用户名")
    port = item.get("port", 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("SSH port 必须在 1–65535 范围内")
    for key in ("directory", "env_file"):
        value = item.get(key)
        if (
            not isinstance(value, str)
            or not value.startswith("/")
            or value == "/"
            or ".." in PurePosixPath(value).parts
            or re.fullmatch(r"/[a-zA-Z0-9_./-]+", value) is None
        ):
            raise ValueError(
                f"{app}.{key} 必须是目标主机绝对路径（仅字母、数字、下划线、点、斜杠和短横线）"
            )
    if PurePosixPath(item["env_file"]).is_relative_to(item["directory"]):
        raise ValueError("env_file 必须放在部署目录之外")
    return {key: item[key] for key in ("host", "user", "directory", "env_file")} | {"port": port}


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="通过 SSH 管理 Northstar 数据库和应用的部署、启停、状态及日志"
    )
    parser.add_argument(
        "action",
        choices=("deploy", "start", "restart", "status", "logs", "stop"),
        help="部署、启动、重启、状态、容器日志、停止（保留数据）",
    )
    parser.add_argument("app", choices=APPLICATIONS)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "deploy/hosts.toml",
        help="主机配置 TOML，默认 deploy/hosts.toml",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅显示目标，不连接 SSH")
    parser.add_argument("--follow", action="store_true", help="持续查看容器标准输出（仅 logs）")
    args = parser.parse_args()
    if args.follow and args.action != "logs":
        parser.error("--follow 仅用于 logs")
    try:
        config = configuration(args.config, args.app)
        revision = None
        if args.action == "deploy":
            if git("status", "--porcelain", "--untracked-files=all"):
                raise ValueError("部署要求干净的 Git 工作区，请先提交修改")
            revision = git("rev-parse", "HEAD")
        request = config | {
            "app": args.app,
            "action": args.action,
            "revision": revision,
            "follow": args.follow,
        }
        print(
            f"{args.action} {args.app} → {config['user']}@{config['host']}:{config['port']} "
            f"{config['directory']}" + (f" @{revision}" if revision else ""),
            flush=True,
        )
        if args.dry_run:
            return 0
        # Strict host verification; authenticate using the user's SSH agent/config.
        command = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-p",
            str(config["port"]),
            "-l",
            config["user"],
            config["host"],
            shlex.join(
                [
                    "python3",
                    "-c",
                    (ROOT / "scripts/northstarctl_remote.py").read_text(),
                    json.dumps(request),
                ]
            ),
        ]
        if args.action != "deploy":
            return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode
        with tempfile.TemporaryDirectory(prefix="northstar-deploy-") as temporary:
            bundle = Path(temporary) / "source.bundle"
            subprocess.run(
                ["git", "-C", str(ROOT), "bundle", "create", str(bundle), "HEAD"], check=True
            )
            # Refuse a racing commit/edit rather than transferring a different revision.
            if git("rev-parse", "HEAD") != revision or git("status", "--porcelain"):
                raise ValueError("打包期间 Git 工作区发生变化，请重新部署")
            with bundle.open("rb") as source:
                return subprocess.run(command, stdin=source, check=False).returncode
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"部署失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
