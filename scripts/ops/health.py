#!/usr/bin/env python3
"""从 Windows 或 Linux 工作站读取 Linux 目标服务的健康状态。"""

from __future__ import annotations

import argparse
from pathlib import Path

from _remote import RemoteOperationError, load_deployment_inventory, run_linux_operation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="读取 Northstar Quant Linux 服务健康状态。")
    parser.add_argument("--inventory", type=Path, default=Path("deploy.env"), help="部署清单路径。")
    parser.add_argument("--dry-run", action="store_true", help="仅显示目标，不建立 SSH 连接。")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        inventory = load_deployment_inventory(args.inventory)
        return run_linux_operation(
            inventory=inventory,
            operation="health",
            arguments=(inventory.systemd_service_name,),
            dry_run=args.dry_run,
        )
    except RemoteOperationError as exc:
        print(f"远程健康检查失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
