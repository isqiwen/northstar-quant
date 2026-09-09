"""Data command arguments and calls to the owning operations."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Engine

from northstar_quant.cli.study import read
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary


def receive(
    arguments: argparse.Namespace, library: DataLibrary, files: SourceFiles
) -> tuple[dict[str, object], dict[str, object]]:
    csv_path, source, parameters, archive = read(arguments.study.resolve())
    required = {"use_basis", "allow_retention", "allow_download", "input_kind"}
    if not required <= set(archive) <= required | {"upstream_source_id", "transformation_note"}:
        raise ValueError(
            "imports require explicit [archive] use_basis, allow_retention, "
            "allow_download and input_kind"
        )
    with csv_path.open("rb") as stream:
        content = stream.read(files.max_file_bytes + 1)
    upstream = archive.get("upstream_source_id")
    attempt = library.receive(
        content,
        filename=csv_path.name,
        source_name=cast(str, source.get("source_name")),
        use_basis=cast(str, archive["use_basis"]),
        allow_retention=cast(bool, archive["allow_retention"]),
        allow_download=cast(bool, archive["allow_download"]),
        input_kind=cast(str, archive["input_kind"]),
        upstream_source_id=None if upstream is None else UUID(str(upstream)),
        transformation_note=cast(str | None, archive.get("transformation_note")),
        spec=source,
        request_id=str(arguments.request_id or uuid4()),
    )
    return (attempt, parameters)


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("import", help="导入文件并发布数据快照，不运行研究")
    parser.set_defaults(scope="data", operation="import")
    parser.add_argument("study", type=Path, help="TOML study with [source] and [research]")
    parser.add_argument("--request-id", type=UUID, help="重试时复用同一请求 UUID")
    parser = commands.add_parser(
        "sync-tushare", help="提交 Tushare 历史同步任务，由独立 worker 下载"
    )
    parser.set_defaults(scope="data", operation="sync-tushare")
    parser.add_argument("config", type=Path, help="仅包含 [source] 市场和时段参数的 TOML")
    parser.add_argument("--request-id", required=True, type=UUID, help="本次接收的固定请求 UUID")
    parser = commands.add_parser("sync", help="查询历史同步任务及对应加工身份")
    parser.set_defaults(scope="data", operation="sync")
    parser.add_argument("request_id", type=UUID)
    parser = commands.add_parser("sources", help="列出来源文件")
    parser.set_defaults(scope="data", operation="sources")
    parser = commands.add_parser("source", help="查看来源文件及使用记录")
    parser.set_defaults(scope="data", operation="source")
    parser.add_argument("source_id", type=UUID)
    parser = commands.add_parser("attempt", help="查看加工结果或失败原因")
    parser.set_defaults(scope="data", operation="attempt")
    parser.add_argument("attempt_id", type=UUID)
    parser = commands.add_parser("reprocess", help="使用明确参数重新加工已留存文件")
    parser.set_defaults(scope="data", operation="reprocess")
    parser.add_argument("source_id", type=UUID)
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--request-id", required=True, type=UUID)
    parser = commands.add_parser("download", help="导出允许下载的原始文件")
    parser.set_defaults(scope="data", operation="download")
    parser.add_argument("source_id", type=UUID)
    parser.add_argument("destination", type=Path)
    parser = commands.add_parser("datasets", help="列出已发布的数据快照")
    parser.set_defaults(scope="data", operation="datasets")
    parser = commands.add_parser("dataset", help="查看快照、质量与时间信息")
    parser.set_defaults(scope="data", operation="dataset")
    parser.add_argument("snapshot_id", type=UUID)


def execute(arguments: argparse.Namespace, engine: Engine) -> int:
    files = SourceFiles.from_environment()
    library = DataLibrary(engine, files)
    if arguments.operation == "sync-tushare":
        from northstar_quant.data_management.research import ImportSpec
        from northstar_quant.data_management.tushare import submit

        with arguments.config.open("rb") as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError("Tushare configuration exceeds 64 KiB")
        document = tomllib.loads(content.decode("utf-8"))
        if set(document) != {"source"}:
            raise ValueError("Tushare configuration requires only [source]")
        result = submit(
            engine, ImportSpec.from_mapping(document["source"]), request_id=arguments.request_id
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if arguments.operation == "sync":
        from northstar_quant.data_management.tushare import get

        print(json.dumps(get(engine, arguments.request_id), ensure_ascii=False))
        return 0
    if arguments.operation == "import":
        attempt, _ = receive(arguments, library, files)
        print(json.dumps(attempt, ensure_ascii=False))
        return 0 if attempt["status"] == "PUBLISHED" else 2
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
    if arguments.operation == "reprocess":
        attempt = library.reprocess(
            arguments.source_id,
            spec=read(arguments.study.resolve())[1],
            request_id=str(arguments.request_id),
        )
        print(json.dumps(attempt, ensure_ascii=False))
        return 0 if attempt["status"] == "PUBLISHED" else 2
    if arguments.operation == "download":
        filename, content = library.download(arguments.source_id)
        with arguments.destination.open("xb") as stream:
            stream.write(content)
        print(json.dumps({"filename": filename, "byte_count": len(content)}))
        return 0
    raise ValueError("未知命令操作")
