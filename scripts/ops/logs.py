#!/usr/bin/env python3
"""从 Windows 或 Linux 工作站读取 Linux systemd 服务日志。"""

from __future__ import annotations

import argparse
from pathlib import Path

from _remote import RemoteOperationError, load_deployment_inventory, run_linux_operation


def _line_count(value: str) -> int:
    try:
        lines = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日志行数必须是整数。") from exc
    if not 1 <= lines <= 1_000:
        raise argparse.ArgumentTypeError("日志行数必须在 1 到 1000 之间。")
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="读取 Northstar Quant Linux 服务日志。")
    parser.add_argument("--inventory", type=Path, default=Path("deploy.env"), help="部署清单路径。")
    parser.add_argument("--lines", type=_line_count, default=200, help="读取最后 N 行，默认 200。")
    parser.add_argument("--dry-run", action="store_true", help="仅显示目标，不建立 SSH 连接。")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        inventory = load_deployment_inventory(args.inventory)
        return run_linux_operation(
            inventory=inventory,
            operation="logs",
            arguments=(inventory.systemd_service_name, str(args.lines)),
            dry_run=args.dry_run,
        )
    except RemoteOperationError as exc:
        print(f"读取远程日志失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
