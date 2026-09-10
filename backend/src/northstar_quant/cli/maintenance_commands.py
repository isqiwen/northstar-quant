"""Maintenance command arguments and calls to the owning operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import Engine

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("password-hash", help="交互生成工作台密码摘要，不保存明文")
    parser.set_defaults(scope="maintenance", operation="password-hash")
    parser = commands.add_parser("init-db", help="初始化或检查当前数据库结构")
    parser.set_defaults(scope="maintenance", operation="init-db")
    parser = commands.add_parser("audit-data", help="核查来源文件并恢复中断处理记录，不删除数据")
    parser.set_defaults(scope="maintenance", operation="audit-data")
    parser = commands.add_parser(
        "prune-data", help="预览无引用文件；用 --apply 清单身份执行有限清理"
    )
    parser.set_defaults(scope="maintenance", operation="prune-data")
    parser.add_argument("--apply", metavar="PLAN_ID", help="执行刚预览的清单，引用变化时拒绝")
    parser = commands.add_parser("backup", help="备份数据库及其引用的来源文件")
    parser.set_defaults(scope="maintenance", operation="backup")
    parser.add_argument("destination", type=Path)
    parser = commands.add_parser("restore", help="恢复到空数据库和新的来源目录")
    parser.set_defaults(scope="maintenance", operation="restore")
    parser.add_argument("backup", type=Path)
    parser = commands.add_parser("init-auth", help="创建 Live 内部认证文件，不连接柜台")
    parser.set_defaults(scope="maintenance", operation="init-live-auth")
    parser.add_argument("directory", type=Path)


def execute(arguments: argparse.Namespace, engine: Engine) -> int:
    files = SourceFiles.from_environment()
    library = DataLibrary(engine, files)
    if arguments.operation == "prune-data":
        from northstar_quant.data_management import cleanup

        result = (
            cleanup.execute(engine, files, arguments.apply)
            if arguments.apply
            else cleanup.preview(engine, files)
        )
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("status") in {"FAILED", "STARTED"} else 0
    if arguments.operation == "audit-data":
        print(json.dumps(library.reconcile(), ensure_ascii=False))
        return 0
    if arguments.operation == "backup":
        from northstar_quant.apps.maintenance import backup

        print(
            json.dumps(backup(engine, files, arguments.destination.resolve()), ensure_ascii=False)
        )
        return 0
    raise ValueError("未知命令操作")
