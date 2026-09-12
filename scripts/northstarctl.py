#!/usr/bin/env python3
"""Manage one Northstar application via SSH; credentials remain on its host."""

from __future__ import annotations

import argparse
import json
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = ("database", "data-hub", "research", "live")


def configuration(path: Path, app: str) -> dict:
    settings = tomllib.loads(path.read_text())
    if "database" in settings:
        raise ValueError("请删除 [database]；数据库固定使用 [data_hub] 主机配置")
    owner = "data_hub" if app == "database" else app.replace("-", "_")
    item = settings.get(owner, {})
    host = item.get("host")
    if not isinstance(host, str) or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.:-]*", host):
        raise ValueError(f"{app}.host 必须填写有效的 SSH 主机地址，不带协议前缀")
    if set(item) - {"host"}:
        raise ValueError("主机配置只接受 host；SSH 固定使用 northstar 和 22 端口")
    return {
        "host": host,
        "user": "northstar",
        "port": 22,
        "directory": f"/opt/northstar/apps/{app}",
        "env_file": f"/opt/northstar/config/{app}.env",
    }


def ssh(config: dict, program: str, argument: str) -> list[str]:
    execute = ["python3", "-c", program, argument]
    return [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ControlPath=none",
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
        "northstar",
        config["host"],
        shlex.join(execute),
    ]


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="通过 SSH 管理 Northstar 数据库和应用的部署、启停、状态及日志"
    )
    parser.add_argument(
        "action",
        choices=("deploy", "start", "restart", "status", "logs", "stop"),
        help="部署（检查已准备的主机依赖）、启动、重启、状态、日志、停止（保留数据）",
    )
    parser.add_argument("app", choices=APPLICATIONS, help="需要管理的应用")
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
    parser.add_argument("--follow", action="store_true", help="持续查看日志（仅 logs）")
    parser.add_argument("--instance", help="仅管理指定 Live 实例的启停、状态和日志")
    args = parser.parse_args()
    if args.instance and (
        args.app != "live" or args.action not in {"start", "restart", "stop", "status", "logs"}
    ):
        parser.error("--instance 仅用于 Live 实例启停、状态和日志")
    if args.env_file is not None and args.action != "deploy":
        parser.error("--env-file 仅用于 deploy；其他命令使用已部署的运行配置")
    if args.follow and args.action != "logs":
        parser.error("--follow 仅用于 logs")
    try:
        config = configuration(args.config, args.app)
        settings = tomllib.loads(args.config.read_text())
        nfs = None
        if args.app in {"database", "data-hub", "research"}:
            nfs = runpy.run_path(str(ROOT / "scripts/operations/nfs.py"))["topology"](settings)
        revision = None
        custom_environment = None
        environment_path = args.env_file or ROOT / "deploy" / args.app.replace("-", "_") / ".env"
        source_paths = ["--", ".", f":(exclude)deploy/{args.app.replace('-', '_')}/.env"]
        if args.action == "deploy":
            with environment_path.expanduser().open("rb") as source:
                custom_environment = source.read(1024 * 1024 + 1)
            runpy.run_path(str(ROOT / "scripts/operations/application_configuration.py"))[
                "validate"
            ](args.app, custom_environment)
            if len(custom_environment) > 1024 * 1024:
                raise ValueError("应用配置超过大小限制")
            if git("status", "--porcelain", "--untracked-files=all", *source_paths):
                raise ValueError(
                    "部署要求干净的 Git 源码工作区（所属 .env 独立传输），请先提交代码修改"
                )
            revision = git("rev-parse", "HEAD")
        request = config | {
            "app": args.app,
            "action": args.action,
            "revision": revision,
            "follow": args.follow,
            "replace_configuration": args.env_file is not None,
            "instance": args.instance,
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
        if args.action == "deploy" and nfs:
            print(
                f"NFS：{nfs['server']}:/quant → /opt/northstar/files/market；"
                f"{nfs['writer']} 读写，{nfs['reader']} 只读消费"
            )
        if args.dry_run:
            return 0
        command = ssh(
            config, (ROOT / "scripts/operations/remote.py").read_text(), json.dumps(request)
        )
        if args.action != "deploy":
            return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode
        if nfs:
            program = (ROOT / "scripts/operations/nfs.py").read_text()
            subprocess.run(
                ssh(config, program, json.dumps(nfs | {"host": config["host"], "preflight": True})),
                stdin=subprocess.DEVNULL,
                check=True,
            )
            elevated = (
                "import subprocess,sys; subprocess.run(['sudo','-n','--',"
                "'python3','-c'," + repr(program) + ",sys.argv[1]],check=True)"
            )
            subprocess.run(
                ssh(config, elevated, json.dumps(nfs | {"host": config["host"]})),
                stdin=subprocess.DEVNULL,
                check=True,
            )
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
                            ).read_text(),
                        }
                    ),
                ]
            )
        ]
        interactive = sys.stdin.isatty()
        if interactive:
            bootstrap[1] = "-tt"
        subprocess.run(bootstrap, stdin=None if interactive else subprocess.DEVNULL, check=True)
        # Transfer the committed source after host prerequisites have passed.
        with tempfile.TemporaryDirectory(prefix="northstar-deploy-") as temporary:
            bundle = Path(temporary) / "source.bundle"
            subprocess.run(
                ["git", "-C", str(ROOT), "bundle", "create", str(bundle), "HEAD"], check=True
            )
            # Refuse a racing commit/edit rather than transferring a different revision.
            if git("rev-parse", "HEAD") != revision or git("status", "--porcelain", *source_paths):
                raise ValueError("打包期间 Git 工作区发生变化，请重新部署")
            payload = Path(temporary) / "deployment.payload"
            with payload.open("xb") as output:
                payload.chmod(0o600)
                assert custom_environment is not None
                output.write(len(custom_environment).to_bytes(8, "big"))
                output.write(custom_environment)
                with bundle.open("rb") as source:
                    shutil.copyfileobj(source, output)
            with payload.open("rb") as source:
                return subprocess.run(command, stdin=source, check=False).returncode

    except subprocess.CalledProcessError as error:
        print(
            f"操作失败（退出码 {error.returncode}），请查看上方错误；修复后重新执行当前命令。",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as error:
        print(f"部署失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
