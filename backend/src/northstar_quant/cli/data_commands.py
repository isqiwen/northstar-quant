"""Data command arguments and calls to the owning operations."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from sqlalchemy import Engine

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("sync", help="查看 Tushare 全量自动同步进度")
    parser.set_defaults(scope="data", operation="sync")
    parser = commands.add_parser("sources", help="列出来源文件")
    parser.set_defaults(scope="data", operation="sources")
    parser = commands.add_parser("source", help="查看来源文件及使用记录")
    parser.set_defaults(scope="data", operation="source")
    parser.add_argument("source_id", type=UUID)
    parser = commands.add_parser("attempt", help="查看加工结果或失败原因")
    parser.set_defaults(scope="data", operation="attempt")
    parser.add_argument("attempt_id", type=UUID)
    parser = commands.add_parser("datasets", help="列出已发布的数据快照")
    parser.set_defaults(scope="data", operation="datasets")
    parser = commands.add_parser("dataset", help="查看快照、质量与时间信息")
    parser.set_defaults(scope="data", operation="dataset")
    parser.add_argument("snapshot_id", type=UUID)


def execute(arguments: argparse.Namespace, engine: Engine) -> int:
    files = SourceFiles.from_environment()
    library = DataLibrary(engine, files)
    if arguments.operation == "sync":
        from northstar_quant.data_management.tushare.settings import status

        print(json.dumps(status(engine), ensure_ascii=False))
        return 0
    if arguments.operation == "datasets":
        print(json.dumps([item.to_dict() for item in library.list_datasets()], ensure_ascii=False))
        return 0
    if arguments.operation == "dataset":
        print(
            json.dumps(
                library.describe_dataset(arguments.snapshot_id).to_dict(), ensure_ascii=False
            )
        )
        return 0
    if arguments.operation == "sources":
        print(json.dumps(library.list_sources(), ensure_ascii=False))
        return 0
    if arguments.operation == "source":
        print(json.dumps(library.source(arguments.source_id), ensure_ascii=False))
        return 0
    if arguments.operation == "attempt":
        print(json.dumps(library.attempt(arguments.attempt_id), ensure_ascii=False))
        return 0
    raise ValueError("未知命令操作")
