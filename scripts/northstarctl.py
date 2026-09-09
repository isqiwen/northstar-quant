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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = ("database", "data-hub", "research", "live")


def configuration(path: Path, app: str) -> dict:
    item = tomllib.loads(path.read_text()).get(app.replace("-", "_"), {})
    host = item.get("host")
    if not isinstance(host, str) or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.:-]*", host):
        raise ValueError(f"{app}.host 必须填写有效的 SSH 主机地址，不带协议前缀")
    user = item.get("user")
    if not isinstance(user, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*", user):
        raise ValueError(f"{app}.user 必须填写用于初始化登录及提权的用户名")
    port = item.get("port", 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("SSH port 必须在 1–65535 范围内")
    if set(item) - {"host", "user", "port"}:
        raise ValueError("主机配置只接受 host/user/port；部署账号固定为 northstar")
    return {
        "host": host,
        "user": "northstar",
        "bootstrap_user": user,
        "port": port,
        "directory": f"/opt/northstar/apps/{app}",
        "env_file": f"/opt/northstar/config/{app}.env",
    }


def ssh(config: dict, program: str, argument: str, *, initialize: bool = False) -> list[str]:
    interactive = initialize and sys.stdin.isatty()
    login = config["bootstrap_user"] if initialize else "northstar"
    execute = ["python3", "-c", program, argument]
    if initialize and login != "root":
        execute = ["sudo", *([] if interactive else ["-n"]), "--", *execute]
    return [
        "ssh",
        "-tt" if interactive else "-T",
        "-o",
        "BatchMode=no" if interactive else "BatchMode=yes",
        "-o",
        "ControlPath=none",
        "-o",
        "StrictHostKeyChecking=ask" if interactive else "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-p",
        str(config["port"]),
        "-l",
        login,
        config["host"],
        shlex.join(execute),
    ]


def initialize_hosts(args: argparse.Namespace) -> int:
    settings = tomllib.loads(args.config.read_text())
    apps = (
        [args.app]
        if args.app
        else [app for app in APPLICATIONS if settings.get(app.replace("-", "_"), {}).get("host")]
    )
    targets = {}
    for app in apps:
        config = configuration(args.config, app)
        identity = (config["host"], config["port"])
        if identity in targets and targets[identity]["bootstrap_user"] != config["bootstrap_user"]:
            raise ValueError("同一主机的初始化 user 必须一致")
        targets[identity] = config
    if not targets:
        raise ValueError("没有已配置的目标主机")
    if args.dry_run:
        for (host, port), config in targets.items():
            print(f"init-host → {config['bootstrap_user']}@{host}:{port}，准备 northstar 部署账号")
        return 0
    key = next(
        (
            p
            for p in (Path.home() / ".ssh/id_ed25519.pub", Path.home() / ".ssh/id_rsa.pub")
            if p.is_file()
        ),
        None,
    )
    if key is None:
        raise ValueError("未找到 SSH 公钥，请先执行 ssh-keygen -t ed25519 创建密钥")
    public_key = key.read_text().strip()
    fields = public_key.split()
    if (
        len(public_key.splitlines()) != 1
        or len(public_key) > 16384
        or len(fields) < 2
        or not fields[0].startswith(("ssh-", "ecdsa-", "sk-"))
    ):
        raise ValueError("请提供单行 SSH 公钥，不能提供私钥")
    subprocess.run(["ssh-keygen", "-lf", str(key)], check=True, stdout=subprocess.DEVNULL)
    for config in targets.values():
        print(
            f"init-host → {config['bootstrap_user']}@{config['host']}:{config['port']}", flush=True
        )
        subprocess.run(
            ssh(
                config,
                (ROOT / "scripts/operations/host_account.py").read_text(),
                json.dumps({"public_key": public_key}),
                initialize=True,
            ),
            stdin=None if sys.stdin.isatty() else subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            ssh(
                config,
                "import os, subprocess; assert os.geteuid() != 0; "
                "subprocess.run(['sudo', '-n', 'true'], check=True)",
                "",
            ),
            stdin=subprocess.DEVNULL,
            check=True,
        )
        print(f"{config['host']}：northstar 密钥登录和 sudo 验证通过", flush=True)
    return 0


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="通过 SSH 管理 Northstar 数据库和应用的部署、启停、状态及日志"
    )
    parser.add_argument(
        "action",
        choices=("init-host", "deploy", "start", "restart", "status", "logs", "stop"),
        help="首次主机初始化、部署（自动准备主机依赖）、启动、重启、状态、容器日志、停止（保留数据）",
    )
    parser.add_argument(
        "app", nargs="?", choices=APPLICATIONS, help="init-host 省略时初始化所有已配置主机"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "deploy/hosts.toml",
        help="主机配置 TOML，默认 deploy/hosts.toml",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="deploy 使用的本地 .env；指定时更新远程配置，默认仅首次安装仓库配置",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅显示目标，不连接 SSH")
    parser.add_argument("--follow", action="store_true", help="持续查看容器标准输出（仅 logs）")
    args = parser.parse_args()
    if args.env_file is not None and args.action != "deploy":
        parser.error("--env-file 仅用于 deploy；其他命令使用已部署的运行配置")
    if args.follow and args.action != "logs":
        parser.error("--follow 仅用于 logs")
    if args.action != "init-host" and args.app is None:
        parser.error("应用管理命令必须指定应用")
    try:
        if args.action == "init-host":
            return initialize_hosts(args)
        config = configuration(args.config, args.app)
        revision = None
        custom_environment = None
        if args.env_file is not None:
            with args.env_file.expanduser().open("rb") as source:
                custom_environment = source.read(1024 * 1024 + 1)
            if len(custom_environment) > 1024 * 1024:
                raise ValueError("应用配置超过大小限制")
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
        if args.action == "deploy":
            source_name = (
                args.env_file
                if args.env_file is not None
                else f"deploy/{args.app.replace('-', '_')}/.env"
            )
            print(
                f"应用配置：{source_name}；"
                + (
                    "更新远程运行配置"
                    if args.env_file is not None
                    else "仅首次安装，保留已有运行配置"
                ),
                flush=True,
            )
        if args.dry_run:
            return 0
        command = ssh(
            config, (ROOT / "scripts/operations/remote.py").read_text(), json.dumps(request)
        )
        if args.action != "deploy":
            return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode
        bootstrap = command[:-1] + [
            shlex.join(
                [
                    "python3",
                    "-c",
                    (ROOT / "scripts/operations/dependencies.py").read_text(),
                    json.dumps(
                        request
                        | {
                            "directory_program": (
                                ROOT / "scripts/operations/host_directories.py"
                            ).read_text()
                        }
                    ),
                ]
            )
        ]
        interactive = sys.stdin.isatty()
        if interactive:
            bootstrap[1] = "-tt"
        subprocess.run(bootstrap, stdin=None if interactive else subprocess.DEVNULL, check=True)
        # Reconnect so Docker group membership from first installation takes effect.
        with tempfile.TemporaryDirectory(prefix="northstar-deploy-") as temporary:
            bundle = Path(temporary) / "source.bundle"
            subprocess.run(
                ["git", "-C", str(ROOT), "bundle", "create", str(bundle), "HEAD"], check=True
            )
            # Refuse a racing commit/edit rather than transferring a different revision.
            if git("rev-parse", "HEAD") != revision or git("status", "--porcelain"):
                raise ValueError("打包期间 Git 工作区发生变化，请重新部署")
            template = Path(temporary) / "application.env"
            folder = args.app.replace("-", "_")
            with template.open("xb") as output:
                template.chmod(0o600)
                if custom_environment is not None:
                    output.write(custom_environment)
                else:
                    subprocess.run(
                        ["git", "-C", str(ROOT), "show", f"{revision}:deploy/{folder}/.env"],
                        stdout=output,
                        check=True,
                    )
            upload = command[:-1] + [
                shlex.join(
                    [
                        "python3",
                        "-c",
                        (ROOT / "scripts/operations/upload_configuration.py").read_text(),
                        config["env_file"],
                        *(["--replace"] if custom_environment is not None else []),
                    ]
                )
            ]
            with template.open("rb") as source:
                subprocess.run(upload, stdin=source, check=True)
            with bundle.open("rb") as source:
                return subprocess.run(command, stdin=source, check=False).returncode
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"部署失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
