"""Small public command tree; each family owns its arguments and implementation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from uuid import UUID

from northstar_quant.cli import (
    broker_commands,
    data_commands,
    maintenance_commands,
    paper_commands,
    research_commands,
    stream_commands,
)


def group(
    commands: argparse._SubParsersAction[argparse.ArgumentParser], name: str, help: str
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    parser = commands.add_parser(name, help=help, description=help)
    return parser.add_subparsers(required=True, title="操作")


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="northstar",
        description="Northstar：启动、检查和维护。日常数据、研究与 Live 控制请使用网页。",
        epilog=(
            "使用 northstar <分组> --help 查看操作。结果输出 JSON；"
            "成功返回 0，错误或未满足条件返回 2。"
        ),
    )
    commands = parser.add_subparsers(required=True, title="命令")
    servers = group(commands, "serve", "启动一个后端服务（前端通过 Compose 或 npm 启动）")
    servers.add_parser("data-worker", help="独立执行已持久接收的数据加工任务").set_defaults(
        scope="serve", operation="data-worker"
    )
    for name, role, port in (
        ("data-api", "data-hub", 19082),
        ("research-api", "research-web", 19084),
        ("live-api", "live-web", 19080),
        ("live-kernel", "live", 18081),
    ):
        service = servers.add_parser(name, help=f"启动 {name}，默认端口 {port}")
        service.add_argument("--port", type=int, default=port)
        service.set_defaults(scope="serve", operation=role)
    for name, operation, help in (
        ("status", "live-status", "查看 Live 内核运行状态，不连接柜台"),
        ("check", "live-check", "检查 Live 内核与存储，异常时返回非零退出码"),
    ):
        commands.add_parser(name, help=help).set_defaults(scope="runtime", operation=operation)
    data_commands.register(group(commands, "data", "数据导入、查询和导出"))
    research = group(commands, "research", "运行研究、查询结果与管理文件回放 Paper")
    research_commands.register(research)
    paper_commands.register(group(research, "paper", "文件回放模拟账户，不操作真实账户"))
    maintenance_commands.register(group(commands, "maintenance", "数据库、认证与备份恢复维护"))
    advanced = group(commands, "advanced", "高级运维：柜台证据处理、显式接收控制与未知命令查询")
    broker_commands.register(
        group(advanced, "broker", "柜台查询和已保存账户证据处理；不授予交易权限")
    )
    stream_commands.register(group(advanced, "stream", "有时限的接收、控制、观察与归档"))
    receipt = advanced.add_parser("receipt", help="查询固定命令身份的结果，绝不重新提交")
    receipt.add_argument("request_id", type=UUID)
    receipt.set_defaults(scope="runtime", operation="live-command-show")
    return parser.parse_args(argv)
