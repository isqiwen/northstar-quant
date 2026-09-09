"""Paper command arguments and calls to the owning operations."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from sqlalchemy import Engine

from northstar_quant.data_management.publication_client import PublicationClient
from northstar_quant.research.paper import PaperStore


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("create", help="创建暂停的文件回放 Paper 账户")
    parser.set_defaults(scope="paper", operation="paper-create")
    parser.add_argument("snapshot_id", type=UUID)
    parser.add_argument("configuration_id")
    parser.add_argument("--request-id", type=UUID, required=True, help="stable retry identity")
    parser = commands.add_parser("list", help="列出记录")
    parser.set_defaults(scope="paper", operation="paper-list")
    parser = commands.add_parser("show", help="查看记录")
    parser.set_defaults(scope="paper", operation="paper-show")
    parser.add_argument("session_id", type=UUID)
    parser = commands.add_parser("next", help="核对并推进一条文件输入；重试须复用请求身份")
    parser.set_defaults(scope="paper", operation="paper-next")
    parser.add_argument("session_id", type=UUID)
    parser.add_argument("--request-id", type=UUID, required=True, help="reuse this UUID on retry")


def execute(arguments: argparse.Namespace, engine: Engine) -> int:
    library = PublicationClient.from_environment()
    paper = PaperStore(engine, library)
    if arguments.operation == "paper-create":
        print(
            json.dumps(
                paper.create(
                    arguments.snapshot_id,
                    arguments.configuration_id,
                    request_id=arguments.request_id,
                ),
                ensure_ascii=False,
            )
        )
        return 0
    if arguments.operation == "paper-list":
        print(json.dumps(paper.list(), ensure_ascii=False))
        return 0
    if arguments.operation == "paper-show":
        print(json.dumps(paper.get(arguments.session_id), ensure_ascii=False))
        return 0
    if arguments.operation == "paper-next":
        print(
            json.dumps(
                paper.advance(arguments.session_id, request_id=arguments.request_id),
                ensure_ascii=False,
            )
        )
        return 0
    raise ValueError("未知命令操作")
