"""Research command arguments and calls to the owning operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from northstar_quant.cli.study import read
from northstar_quant.data_management.publication_client import PublicationClient
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.research.operations import ResearchOperations
from northstar_quant.research.runs import RunStore


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("tasks", help="列出持久研究任务")
    parser.set_defaults(scope="research", operation="tasks")
    for name, description in (
        ("task", "查看任务与尝试"),
        ("cancel", "请求取消任务"),
        ("retry", "重试中断或失败任务的固定输入"),
    ):
        parser = commands.add_parser(name, help=description)
        parser.set_defaults(scope="research", operation=name)
        parser.add_argument("task_id", type=UUID)
    parser = commands.add_parser("run", help="使用已发布快照运行研究")
    parser.set_defaults(scope="research", operation="research")
    parser.add_argument("snapshot_id", type=UUID)
    parser.add_argument(
        "--study",
        type=Path,
        help="use only this TOML study's [research] parameters; do not import CSV",
    )
    parser = commands.add_parser("replay", help="按固定输入重放已保存的研究结果")
    parser.set_defaults(scope="research", operation="replay")
    parser.add_argument("run_id")
    parser = commands.add_parser("show", help="查看记录")
    parser.set_defaults(scope="research", operation="show")
    parser.add_argument("run_id")
    parser = commands.add_parser("list", help="列出记录")
    parser.set_defaults(scope="research", operation="list")
    parser = commands.add_parser("configure", help="保存不可变策略与风险配置")
    parser.set_defaults(scope="research", operation="configure")
    parser.add_argument("study", type=Path)
    parser.add_argument("--name", required=True, help="human-readable configuration label")
    parser = commands.add_parser("configurations", help="列出已保存的配置")
    parser.set_defaults(scope="research", operation="configurations")


def execute(arguments: argparse.Namespace, engine: Engine) -> int:
    result: object
    if arguments.operation in {"tasks", "task", "cancel", "retry"}:
        from northstar_quant.research.tasks.store import TaskStore

        tasks = TaskStore(engine)
        if arguments.operation == "tasks":
            result = tasks.list()
        elif arguments.operation == "task":
            result = tasks.get(str(arguments.task_id))
        else:
            result = tasks.control(str(arguments.task_id), arguments.operation)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if arguments.operation == "configure":
        config = ResearchConfig.from_mapping(read(arguments.study.resolve())[2])
        print(
            json.dumps(
                ConfigurationStore(engine).save_configuration(arguments.name, config),
                ensure_ascii=False,
            )
        )
        return 0
    if arguments.operation == "configurations":
        print(json.dumps(ConfigurationStore(engine).list_configurations(), ensure_ascii=False))
        return 0
    if arguments.operation in {"show", "list"}:
        store = RunStore(engine)
        result = store.get(arguments.run_id) if arguments.operation == "show" else store.list()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    library = PublicationClient.from_environment()
    store = RunStore(engine)
    research = ResearchOperations(library, store)
    if arguments.operation == "research":
        config = (
            ResearchConfig()
            if arguments.study is None
            else ResearchConfig.from_mapping(read(arguments.study.resolve())[2])
        )
        run_id = research.run(arguments.snapshot_id, config)
        print(json.dumps(store.get(run_id), ensure_ascii=False, sort_keys=True))
        return 0
    if arguments.operation == "replay":
        print(json.dumps(research.replay(arguments.run_id), ensure_ascii=False, sort_keys=True))
        return 0
    raise ValueError("未知命令操作")
