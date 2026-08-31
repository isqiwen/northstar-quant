#!/usr/bin/env python3
"""从 Linux x86_64 控制主机收集 Linux 服务的只读诊断摘要。"""

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
    parser = argparse.ArgumentParser(description="收集 Northstar Quant Linux 服务的只读诊断摘要。")
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
            operation="diagnose",
            arguments=(inventory.systemd_service_name, inventory.app_root),
            dry_run=args.dry_run,
        )
    except PlatformSupportError as exc:
        print(f"运维控制主机不受支持：{exc}")
        return 1
    except RemoteOperationError as exc:
        print(f"远程诊断失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
