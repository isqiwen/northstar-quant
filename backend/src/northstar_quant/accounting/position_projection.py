"""Rebuild broker position evidence from retained queries and stream prefixes.

Ingestion and recovery use this one derivation. Contract resolution belongs to
Data; recovery injects verification only and cannot repair catalog identities.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from northstar_quant.accounting.positions import PositionChange, project_intraday_positions
from northstar_quant.broker.account_reports import (
    decode_trade,
    position_observations,
    query_trades,
    stream_trades,
)
from northstar_quant.broker.events import ACCOUNT_ACTIVITY_CALLBACKS
from northstar_quant.data_management.broker import BrokerContract


def _time(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() != UTC.utcoffset(moment):
        raise ValueError("ledger time must use UTC")
    return moment


def _economic(fill: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in fill.items()
        if key
        not in {
            "contract_id",
            "source_batch_id",
            "source_sequence",
            "source_stream_id",
            "source_received_at",
        }
    }


def _problems(batch: dict[str, Any], day: str) -> list[dict[str, Any]]:
    reasons = []
    if batch["status"] != "COMPLETE":
        reasons.append("QUERY_NOT_COMPLETE")
    if batch["completeness"]["identity"] != "CONFIRMED":
        reasons.append("TD_ACCOUNT_IDENTITY_NOT_CONFIRMED")
    if batch["completeness"]["trading_day"] != day:
        reasons.append("SETTLEMENT_AND_NEW_TRADING_DAY_NOT_SUPPORTED")
    return [{"code": reason, "source_batch_id": batch["batch_id"]} for reason in reasons]


def derive_position_entry(
    history: list[dict[str, Any]],
    batch: dict[str, Any],
    trading_day: str,
    prefix: dict[str, Any] | None,
    after_sequence: int,
    resolve_contract: Callable[[], BrokerContract],
) -> dict[str, Any]:
    source_batch_id = UUID(batch["batch_id"])
    stream_id = None if prefix is None else UUID(prefix["stream_id"])
    known = {fill["fill_id"]: fill for item in history for fill in item["added_fills"]}
    previous_fill_ids = set(known)
    queried_fill_ids = set()
    queried_sequences = (
        set()
        if prefix is not None
        else {
            event["sequence"]
            for event in batch["capture"]["events"]
            if event["callback"] == "OnRspQryTrade"
        }
    )
    problems = [problem for item in history for problem in item["new_problems"]]
    if prefix is None:
        observations = query_trades(batch)
        new_problems = _problems(batch, trading_day)
    else:
        observations, new_problems = stream_trades(prefix, after_sequence, trading_day)
    added, duplicate_count = [], 0
    stream_receipts = (
        {}
        if prefix is None
        else {item["event"]["sequence"]: item["event"]["received_at"] for item in prefix["events"]}
    )
    contract = None
    for sequence, row in observations:
        locator = {"source_batch_id": str(source_batch_id), "sequence": sequence}
        if prefix is not None:
            locator["source_stream_id"] = str(stream_id)
        try:
            fill = decode_trade(row, batch)
        except ValueError:
            new_problems.append({"code": "TRADE_FIELDS_NOT_CONFIRMED", **locator})
            continue
        if sequence in queried_sequences:
            queried_fill_ids.add(fill["fill_id"])
        earlier = known.get(fill["fill_id"])
        if earlier is not None:
            if _economic(earlier) != fill:
                new_problems.append(
                    {
                        "code": "TRADE_IDENTITY_CONFLICT",
                        "fill_id": fill["fill_id"],
                        **locator,
                    }
                )
            else:
                duplicate_count += 1
            continue
        contract_id = None
        try:
            if fill["exchange"] != "SHFE" or fill["symbol"] != batch["instrument"].upper():
                raise ValueError("trade has no confirmed supported contract")
            if contract is None:
                contract = resolve_contract()
            contract_id = str(contract.contract_id)
        except ValueError:
            new_problems.append({"code": "CANONICAL_CONTRACT_NOT_CONFIRMED", **locator})
        if (
            fill["trading_day"] != trading_day
            or fill["hedge_flag"] != "1"
            or fill["offset"] == "UNSUPPORTED"
        ):
            new_problems.append({"code": "UNSUPPORTED_POSITION_EFFECT", **locator})
        fill.update(
            contract_id=contract_id,
            source_batch_id=str(source_batch_id),
            source_sequence=sequence,
        )
        if prefix is not None:
            fill.update(
                source_stream_id=str(stream_id),
                source_received_at=stream_receipts[sequence],
            )
        known[fill["fill_id"]] = fill
        added.append(fill)
    if prefix is None and previous_fill_ids - queried_fill_ids:
        new_problems.append(
            {
                "code": "RECORDED_TRADES_MISSING_FROM_LATER_QUERY",
                "source_batch_id": str(source_batch_id),
            }
        )
    if len(known) > 10000:
        raise ValueError("position ledger exceeds its bounded daily fill limit")
    problems.extend(new_problems)
    positions: list[dict[str, Any]] = []
    # A cash transfer does not alter contract quantities or FIFO prices. Preserve
    # known inventory while the account as a whole remains unreconciled.
    position_unknown = any(
        problem["code"] != "STREAM_CASHFLOW_RECONCILIATION_REQUIRED" for problem in problems
    )
    if not position_unknown:
        try:
            projection = project_intraday_positions(
                date.fromisoformat(trading_day),
                tuple(
                    PositionChange(
                        UUID(fill["contract_id"]),
                        date.fromisoformat(fill["trading_day"]),
                        fill["direction"],
                        fill["offset"],
                        fill["quantity_lots"],
                        _time(fill["filled_at"]),
                    )
                    for fill in known.values()
                ),
            )
            for projected_contract_id, amounts in projection.items():
                fact = next(
                    fill
                    for fill in known.values()
                    if fill["contract_id"] == str(projected_contract_id)
                )
                for direction in ("LONG", "SHORT"):
                    positions.append(
                        {
                            "contract_id": str(projected_contract_id),
                            "exchange": fact["exchange"],
                            "symbol": fact["symbol"],
                            "hedge_flag": "1",
                            "direction": direction,
                            "today_lots": amounts[f"{direction.lower()}_today"],
                            "yesterday_lots": amounts[f"{direction.lower()}_yesterday"],
                        }
                    )
        except ValueError:
            problem = {
                "code": "POSITION_EFFECTS_CANNOT_BE_RESOLVED",
                "source_batch_id": str(source_batch_id),
            }
            new_problems.append(problem)
            problems.append(problem)
            position_unknown = True
    return {
        "added_fills": added,
        "fill_count": len(known),
        "new_fill_count": len(added),
        "duplicate_count": duplicate_count,
        "new_problems": new_problems,
        "problems": problems,
        "status": "UNKNOWN" if problems else "READY",
        "position_projection": {
            "status": "UNKNOWN" if position_unknown else "KNOWN",
            "positions": positions,
        },
    }


def derive_position_check(
    history: list[dict[str, Any]],
    batch: dict[str, Any],
    trading_day: str,
) -> dict[str, Any]:
    entry = history[-1]
    known = {fill["fill_id"]: fill for item in history for fill in item["added_fills"]}
    problems = list(entry["problems"]) + _problems(batch, trading_day)
    unrecorded = []
    observed: dict[str, dict[str, Any]] = {}
    for sequence, row in query_trades(batch):
        try:
            fill = decode_trade(row, batch)
        except ValueError:
            problems.append({"code": "TRADE_FIELDS_NOT_CONFIRMED", "sequence": sequence})
            continue
        if fill["fill_id"] in observed and observed[fill["fill_id"]] != fill:
            problems.append({"code": "TRADE_IDENTITY_CONFLICT", "fill_id": fill["fill_id"]})
        observed[fill["fill_id"]] = fill
        if fill["fill_id"] not in known:
            if fill not in unrecorded:
                unrecorded.append(fill)
        elif _economic(known[fill["fill_id"]]) != fill:
            problems.append({"code": "TRADE_IDENTITY_CONFLICT", "fill_id": fill["fill_id"]})
    if set(known) - set(observed):
        problems.append({"code": "RECORDED_TRADES_MISSING_FROM_LATER_QUERY"})
    if any(event["callback"] in ACCOUNT_ACTIVITY_CALLBACKS for event in batch["capture"]["events"]):
        problems.append({"code": "ACCOUNT_ACTIVITY_DURING_QUERY"})
    positions, position_problems = _compare_positions(entry, batch)
    problems.extend(position_problems)
    orders = batch["completeness"]["sections"]["orders"]
    if orders["status"] != "COMPLETE":
        problems.append({"code": "ORDERS_NOT_COMPLETE"})
    changed = unrecorded or any(row["delta_today"] or row["delta_yesterday"] for row in positions)
    return {
        "status": "UNKNOWN" if problems else "DIFFERENCES" if changed else "MATCHED",
        "positions": positions,
        "problems": problems,
        "unrecorded_fills": unrecorded,
        "observed_orders": orders["rows"] if orders["status"] == "COMPLETE" else None,
        "observed_positions": batch["completeness"]["sections"]["positions"]["rows"],
        "scope": "POSITION_QUANTITIES_ONLY",
        "cash_projection": None,
        "reconciliation": "UNRECONCILED",
        "execution": {"order_sending": False, "cancel_sending": False},
        "limitations": [
            "NO_CONFIRMED_FEES_CASHFLOW_OR_SETTLEMENT_LEDGER",
            "NO_ORDER_LIFECYCLE_RECONCILIATION",
            "QUERIES_ARE_NOT_ATOMIC",
            "NO_CONTINUOUS_EVENT_COVERAGE_OR_CURRENT_SAFETY_CLAIM",
        ],
    }


def _compare_positions(
    entry: dict[str, Any], batch: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected = {
        (item["exchange"], item["symbol"], item["hedge_flag"], item["direction"]): item
        for item in entry["position_projection"]["positions"]
    }
    observed, complete, problems = position_observations(batch)
    result = []
    for key in sorted(set(expected) | set(observed)):
        item = expected.get(key)
        values = observed.get(key, {"today": 0, "yesterday": 0})
        expected_today: int | None = 0 if item is None else item["today_lots"]
        expected_yesterday: int | None = 0 if item is None else item["yesterday_lots"]
        if entry["position_projection"]["status"] != "KNOWN":
            expected_today = expected_yesterday = None
        observed_today, observed_yesterday = (
            (values["today"], values["yesterday"]) if complete else (None, None)
        )
        result.append(
            {
                "contract_id": None if item is None else item["contract_id"],
                "exchange": key[0],
                "symbol": key[1],
                "hedge_flag": key[2],
                "direction": key[3],
                "expected_today": expected_today,
                "expected_yesterday": expected_yesterday,
                "observed_today": observed_today,
                "observed_yesterday": observed_yesterday,
                "delta_today": None
                if observed_today is None or expected_today is None
                else observed_today - expected_today,
                "delta_yesterday": None
                if observed_yesterday is None or expected_yesterday is None
                else observed_yesterday - expected_yesterday,
            }
        )
    return result, problems
