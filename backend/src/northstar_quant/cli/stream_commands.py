"""Bounded reception requests and observations; the CLI owns no receiving worker."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from northstar_quant.live import LiveClient


def execute(arguments: argparse.Namespace, client: LiveClient) -> int:
    streams = client.streams
    if arguments.operation == "stream-list":
        print(json.dumps(streams.list(), ensure_ascii=False))
    elif arguments.operation == "stream-show":
        print(json.dumps(streams.get(arguments.stream_id), ensure_ascii=False))
    elif arguments.operation == "stream-events":
        print(
            json.dumps(
                streams.events(arguments.stream_id, after=arguments.after),
                ensure_ascii=False,
            )
        )
    elif arguments.operation == "broker-catchup-stream":
        progress = streams.catchup_account(
            arguments.stream_id,
            arguments.baseline_id,
            arguments.through_sequence,
            request_id=arguments.request_id,
        )
        print(json.dumps(progress, ensure_ascii=False))
        return 0 if progress["status"] == "READY" else 2
    elif arguments.operation == "stream-archive":
        attempt = streams.archive(
            arguments.stream_id,
            through_sequence=arguments.through_sequence,
            session_open=arguments.session_open,
            session_close=arguments.session_close,
            request_id=arguments.request_id,
            allow_download=arguments.allow_download,
        )
        print(json.dumps(attempt, ensure_ascii=False))
        return 0 if attempt["status"] == "PUBLISHED" else 2
    elif arguments.operation == "stream-control":
        print(
            json.dumps(
                streams.control(
                    arguments.stream_id, arguments.action, request_id=arguments.request_id
                ),
                ensure_ascii=False,
            )
        )
    else:
        result = streams.start(
            arguments.query_batch_id,
            arguments.configuration,
            request_id=arguments.request_id,
            duration_seconds=arguments.seconds,
            allow_retention=arguments.allow_retention,
            use_basis=arguments.use_basis,
        )
        print(json.dumps(result, ensure_ascii=False))
    return 0


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "catchup-account",
        help="处理固定范围内已保存的账户回报",
    )
    parser.set_defaults(scope="stream", operation="broker-catchup-stream")
    parser.add_argument("baseline_id", type=UUID)
    parser.add_argument("stream_id", type=UUID)
    parser.add_argument("--through-sequence", type=int, required=True)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("list", help="列出记录")
    parser.set_defaults(scope="stream", operation="stream-list")
    parser = commands.add_parser("show", help="查看记录")
    parser.set_defaults(scope="stream", operation="stream-show")
    parser.add_argument("stream_id", type=UUID)
    parser = commands.add_parser("events", help="查看已接收的原始事件")
    parser.set_defaults(scope="stream", operation="stream-events")
    parser.add_argument("stream_id", type=UUID)
    parser.add_argument("--after", type=int, default=0)
    parser = commands.add_parser("archive", help="将固定事件范围归档并加工分钟数据")
    parser.set_defaults(scope="stream", operation="stream-archive")
    parser.add_argument("stream_id", type=UUID)
    parser.add_argument("--through-sequence", type=int, required=True)
    parser.add_argument("--session-open", required=True, help="first minute start in UTC")
    parser.add_argument("--session-close", required=True, help="last minute end in UTC")
    parser.add_argument("--request-id", type=UUID, required=True)
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="permit local download including account TD callbacks",
    )
    parser = commands.add_parser("start", help="显式启动有时限的接收；命令退出不停止接收")
    parser.set_defaults(scope="stream", operation="stream-start")
    parser.add_argument("query_batch_id", type=UUID)
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--allow-retention", action="store_true", required=True)
    parser.add_argument("--use-basis", required=True)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("control", help="暂停、恢复影子计算或请求停止，不发送订单")
    parser.set_defaults(scope="stream", operation="stream-control")
    parser.add_argument("stream_id", type=UUID)
    parser.add_argument("action", choices=("PAUSE", "RESUME", "STOP"))
    parser.add_argument("--request-id", type=UUID, required=True)
