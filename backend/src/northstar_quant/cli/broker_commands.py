"""Broker evidence commands submitted to the owning Live runtime."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from northstar_quant.live import LiveClient


def budget(arguments: argparse.Namespace, client: LiveClient) -> int:
    budgets = client.opening_budgets
    if arguments.operation == "broker-opening-budget":
        try:
            limit_price = Decimal(arguments.limit_price)
        except InvalidOperation as error:
            raise ValueError("limit price must be an exact decimal string") from error
        budget_result = budgets.create(
            arguments.stream_id,
            arguments.sequence,
            arguments.order_check,
            limit_price=limit_price,
            request_id=arguments.request_id,
        )
    else:
        budget_result = budgets.get(arguments.budget_id)
    print(json.dumps(budget_result, ensure_ascii=False))
    return 0 if budget_result["status"] == "WITHIN_BUDGET" else 2


def execute(arguments: argparse.Namespace, client: LiveClient) -> int:
    broker = client.broker
    if arguments.operation == "broker-query":
        broker_result = broker.query(
            arguments.instrument,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "COMPLETE" else 2
    if arguments.operation == "broker-baseline":
        print(
            json.dumps(
                broker.establish_baseline(
                    arguments.source_batch_id, request_id=arguments.request_id
                ),
                ensure_ascii=False,
            )
        )
        return 0
    if arguments.operation == "broker-compare":
        broker_result = broker.compare_baseline(
            arguments.baseline_id,
            arguments.query_batch_id,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "MATCHED" else 2
    if arguments.operation == "broker-baseline-context":
        print(json.dumps(broker.baseline_context(arguments.query_batch_id), ensure_ascii=False))
        return 0
    if arguments.operation == "broker-ingest":
        broker_result = broker.ingest_positions(
            arguments.baseline_id,
            arguments.source_batch_id,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "READY" else 2
    if arguments.operation == "broker-ingest-stream":
        broker_result = broker.ingest_stream_positions(
            arguments.baseline_id,
            arguments.stream_id,
            arguments.through_sequence,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "READY" else 2
    if arguments.operation in {"broker-funds", "broker-funds-show"}:
        broker_result = (
            broker.get_funds_entry(arguments.entry_id)
            if arguments.operation == "broker-funds-show"
            else broker.observe_funds(
                arguments.baseline_id,
                arguments.source_batch_id,
                request_id=arguments.request_id,
            )
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "OBSERVED" else 2
    if arguments.operation == "broker-ledger":
        print(json.dumps(broker.ledger_context(arguments.query_batch_id), ensure_ascii=False))
        return 0
    if arguments.operation == "broker-positions":
        broker_result = broker.compare_positions(
            arguments.entry_id,
            arguments.query_batch_id,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "MATCHED" else 2
    if arguments.operation == "broker-orders":
        broker_result = broker.check_orders(
            arguments.position_check_id, request_id=arguments.request_id
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "MATCHED" else 2
    if arguments.operation == "broker-list":
        print(json.dumps(broker.list(), ensure_ascii=False))
    else:
        print(json.dumps(broker.get(arguments.batch_id), ensure_ascii=False))
    return 0


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("status", help="检查柜台配置，不连接柜台")
    parser.set_defaults(scope="broker", operation="broker-status")
    parser = commands.add_parser("sdk-check", help="加载并释放柜台 SDK，不建立网络连接")
    parser.set_defaults(scope="broker", operation="broker-sdk-check")
    parser = commands.add_parser("list", help="列出记录")
    parser.set_defaults(scope="broker", operation="broker-list")
    parser = commands.add_parser("show", help="查看记录")
    parser.set_defaults(scope="broker", operation="broker-show")
    parser.add_argument("batch_id", type=UUID)
    parser = commands.add_parser("query", help="显式发起有时限的 SimNow 只读查询")
    parser.set_defaults(scope="broker", operation="broker-query")
    parser.add_argument("--instrument", required=True, help="one concrete futures instrument")
    parser.add_argument(
        "--request-id", type=UUID, required=True, help="reuse to read an uncertain response"
    )
    parser = commands.add_parser("baseline", help="根据已保存查询建立账户基线，不连接柜台")
    parser.set_defaults(scope="broker", operation="broker-baseline")
    parser.add_argument("source_batch_id", type=UUID)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("compare", help="比较已保存查询与账户基线")
    parser.set_defaults(scope="broker", operation="broker-compare")
    parser.add_argument("baseline_id", type=UUID)
    parser.add_argument("query_batch_id", type=UUID)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("baseline-context", help="查看基线条件与已有比较记录")
    parser.set_defaults(scope="broker", operation="broker-baseline-context")
    parser.add_argument("query_batch_id", type=UUID)
    parser = commands.add_parser("ingest", help="将已确认的成交证据提交账本处理")
    parser.set_defaults(scope="broker", operation="broker-ingest")
    parser.add_argument("baseline_id", type=UUID)
    parser.add_argument("source_batch_id", type=UUID)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("ingest-stream", help="处理固定事件范围内已确认的成交")
    parser.set_defaults(scope="broker", operation="broker-ingest-stream")
    parser.add_argument("baseline_id", type=UUID)
    parser.add_argument("stream_id", type=UUID)
    parser.add_argument("--through-sequence", type=int, required=True)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("funds", help="记录已保存查询中的账户资金观察")
    parser.set_defaults(scope="broker", operation="broker-funds")
    parser.add_argument("baseline_id", type=UUID)
    parser.add_argument("source_batch_id", type=UUID)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("funds-show", help="查看资金观察记录")
    parser.set_defaults(scope="broker", operation="broker-funds-show")
    parser.add_argument("entry_id", type=UUID)
    parser = commands.add_parser("ledger", help="查看成交入账与持仓核对记录")
    parser.set_defaults(scope="broker", operation="broker-ledger")
    parser.add_argument("query_batch_id", type=UUID)
    parser = commands.add_parser("positions", help="核对账本持仓与独立查询")
    parser.set_defaults(scope="broker", operation="broker-positions")
    parser.add_argument("entry_id", type=UUID)
    parser.add_argument("query_batch_id", type=UUID)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("orders", help="核对已保存委托与成交记录")
    parser.set_defaults(scope="broker", operation="broker-orders")
    parser.add_argument("position_check_id", type=UUID)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser(
        "opening-budget",
        help="计算一手开仓预算，不授予交易权限",
    )
    parser.set_defaults(scope="broker", operation="broker-opening-budget")
    parser.add_argument("stream_id", type=UUID)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--order-check", type=UUID, required=True)
    parser.add_argument("--limit-price", required=True, help="exact decimal limit price")
    parser.add_argument("--request-id", type=UUID, required=True)
    parser = commands.add_parser("opening-budget-show", help="查看固定的历史预算")
    parser.set_defaults(scope="broker", operation="broker-opening-budget-show")
    parser.add_argument("budget_id", type=UUID)
