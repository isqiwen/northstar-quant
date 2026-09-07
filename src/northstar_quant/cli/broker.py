"""Broker evidence commands submitted to the owning Live runtime."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation

from northstar_quant.live import LiveClient


def budget(arguments: argparse.Namespace, client: LiveClient) -> int:
    budgets = client.opening_budgets
    if arguments.command == "broker-opening-budget":
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
    if arguments.command == "broker-query":
        broker_result = broker.query(
            arguments.profile,
            arguments.instrument,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "COMPLETE" else 2
    if arguments.command == "broker-baseline":
        print(
            json.dumps(
                broker.establish_baseline(
                    arguments.source_batch_id, request_id=arguments.request_id
                ),
                ensure_ascii=False,
            )
        )
        return 0
    if arguments.command == "broker-compare":
        broker_result = broker.compare_baseline(
            arguments.baseline_id,
            arguments.query_batch_id,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "MATCHED" else 2
    if arguments.command == "broker-baseline-context":
        print(json.dumps(broker.baseline_context(arguments.query_batch_id), ensure_ascii=False))
        return 0
    if arguments.command == "broker-ingest":
        broker_result = broker.ingest_positions(
            arguments.baseline_id,
            arguments.source_batch_id,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "READY" else 2
    if arguments.command == "broker-ingest-stream":
        broker_result = broker.ingest_stream_positions(
            arguments.baseline_id,
            arguments.stream_id,
            arguments.through_sequence,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "READY" else 2
    if arguments.command in {"broker-funds", "broker-funds-show"}:
        broker_result = (
            broker.get_funds_entry(arguments.entry_id)
            if arguments.command == "broker-funds-show"
            else broker.observe_funds(
                arguments.baseline_id,
                arguments.source_batch_id,
                request_id=arguments.request_id,
            )
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "OBSERVED" else 2
    if arguments.command == "broker-ledger":
        print(json.dumps(broker.ledger_context(arguments.query_batch_id), ensure_ascii=False))
        return 0
    if arguments.command == "broker-positions":
        broker_result = broker.compare_positions(
            arguments.entry_id,
            arguments.query_batch_id,
            request_id=arguments.request_id,
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "MATCHED" else 2
    if arguments.command == "broker-orders":
        broker_result = broker.check_orders(
            arguments.position_check_id, request_id=arguments.request_id
        )
        print(json.dumps(broker_result, ensure_ascii=False))
        return 0 if broker_result["status"] == "MATCHED" else 2
    if arguments.command == "broker-list":
        print(json.dumps(broker.list(), ensure_ascii=False))
    else:
        print(json.dumps(broker.get(arguments.batch_id), ensure_ascii=False))
    return 0
