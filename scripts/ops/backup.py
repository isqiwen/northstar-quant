#!/usr/bin/env python3
"""读取 Linux PostgreSQL 备份与恢复演练的无秘密就绪证据。

本项目不在应用部署通道中创建数据库备份或执行恢复；实际备份必须由独立、最小权限的
运维系统负责。本脚本只远程调用既有的只读 ``northstar ops backup status`` 门禁。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _remote import (
    PlatformSupportError,
    RemoteOperationError,
    load_deployment_inventory,
    require_linux_x86_64,
    run_linux_operation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="读取 Northstar Quant Linux 的 PostgreSQL 备份/恢复就绪证据。"
    )
    parser.add_argument("--inventory", type=Path, default=Path("deploy.env"), help="部署清单路径。")
    parser.add_argument("--dry-run", action="store_true", help="仅显示目标，不建立 SSH 连接。")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        require_linux_x86_64()
        inventory = load_deployment_inventory(args.inventory)
        return run_linux_operation(
            inventory=inventory,
            operation="backup",
            arguments=(inventory.service_user, inventory.app_root),
            dry_run=args.dry_run,
        )
    except PlatformSupportError as exc:
        print(f"运维控制主机不受支持：{exc}")
        return 1
    except RemoteOperationError as exc:
        print(f"远程备份证据检查失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
