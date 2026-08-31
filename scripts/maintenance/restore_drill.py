#!/usr/bin/env python3
"""显式运行仅限 ``northstar_test`` 的 PostgreSQL 工具链恢复演练。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from northstar_quant.foundation.backup.restore_drill import (
    RestoreDrillError,
    run_test_postgresql_restore_drill,
)
from northstar_quant.foundation.platform_support import PlatformSupportError, require_linux_x86_64
from northstar_quant.foundation.security import redact_text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="对隔离 northstar_test 执行 pg_dump/pg_restore 事务回滚演练。"
    )
    parser.add_argument("--workspace-dir", type=Path, required=True, help="私有且已存在的 archive 工作目录。")
    parser.add_argument(
        "--confirm-test-drill",
        default="",
        help="必须精确传入 YES；此入口绝不接受运行时数据库 URL。",
    )
    return parser


def main() -> int:
    try:
        require_linux_x86_64()
    except PlatformSupportError as exc:
        print(f"恢复演练失败：{redact_text(str(exc))}", file=sys.stderr)
        return 2
    args = _parser().parse_args()
    if args.confirm_test_drill != "YES":
        print("恢复演练必须显式传入 --confirm-test-drill YES。", file=sys.stderr)
        return 2
    database_url = os.getenv("NORTHSTAR_TEST_DATABASE_URL", "").strip()
    if not database_url:
        print("未设置 NORTHSTAR_TEST_DATABASE_URL；拒绝猜测任何数据库。", file=sys.stderr)
        return 2
    try:
        result = run_test_postgresql_restore_drill(
            database_url,
            workspace_dir=args.workspace_dir,
        )
    except (RestoreDrillError, ValueError) as exc:
        print(f"恢复演练失败：{redact_text(str(exc))}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "passed",
                "schema_name": result.schema_name,
                "archive_path": str(result.archive_path),
                "archive_sha256": result.archive_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
