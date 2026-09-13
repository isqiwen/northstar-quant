"""Identify transfer facts once from the same retained source as position ingestion."""

from datetime import datetime
from typing import Any

from northstar_quant.broker.account_reports import stream_trades
from northstar_quant.broker.events import TRANSFER_CALLBACKS
from northstar_quant.broker.transfers import decode_transfer

from .cashflows import CashFlowFact


def _economic(fact: CashFlowFact) -> dict[str, object]:
    return {
        key: value
        for key, value in fact.to_dict().items()
        if key not in {"source_reference", "available_at"}
    }


def derive_cash_flows(
    history: list[dict[str, Any]],
    batch: dict[str, Any],
    prefix: dict[str, Any] | None,
    after_sequence: int,
    opening_at: datetime,
    trading_day: str,
) -> dict[str, Any]:
    known = {
        row["cash_flow_id"]: CashFlowFact.from_dict(row)
        for entry in history
        for row in entry["added_cash_flows"]
    }
    reversed_ids = {fact.reverses_id for fact in known.values() if fact.reverses_id}
    if prefix is None:
        events = batch["capture"]["events"]
        identity_confirmed = batch["completeness"]["identity"] == "CONFIRMED"
        source = f"query:{batch['batch_id']}"
    else:
        events = [item["event"] for item in prefix["events"]]
        _, source_problems = stream_trades(prefix, after_sequence, trading_day.replace("-", ""))
        identity_confirmed = not any(
            problem["code"]
            in {
                "STREAM_TD_IDENTITY_NOT_CONFIRMED",
                "STREAM_ACCOUNT_CONNECTION_ERROR",
                "STREAM_ACCOUNT_RECEIPT_TIME_REGRESSED",
            }
            for problem in source_problems
        )
        source = f"stream:{prefix['stream_id']}"
    added, problems = [], []
    duplicates = 0
    for event in events:
        if event["sequence"] <= after_sequence or event["callback"] not in TRANSFER_CALLBACKS:
            continue
        reference = f"{source}:{event['sequence']}"
        try:
            if not identity_confirmed:
                raise ValueError("source does not establish authenticated TD account")
            fact = decode_transfer(
                event,
                broker_id=batch["profile"]["broker_id"],
                account_id=batch["account_id"],
                trading_day=trading_day,
                source_reference=reference,
                opening_at=opening_at,
            )
            previous = known.get(fact.cash_flow_id)
            if previous is not None:
                if _economic(previous) != _economic(fact):
                    raise ValueError(
                        "transfer identity conflicts with its retained economic effect"
                    )
                duplicates += 1
                continue
            if fact.reverses_id is not None:
                original = known.get(fact.reverses_id)
                if (
                    original is None
                    or original.reverses_id is not None
                    or fact.reverses_id in reversed_ids
                    or original.amount != fact.amount.copy_negate()
                    or original.transferred_at > fact.transferred_at
                ):
                    raise ValueError("reversal has no exact unreversed transfer in this book")
                reversed_ids.add(fact.reverses_id)
            known[fact.cash_flow_id] = fact
            added.append(fact.to_dict())
        except (ValueError, TypeError) as error:
            problems.append(
                {
                    "code": "CASH_FLOW_NOT_CONFIRMED",
                    "source_reference": reference,
                    "reason": str(error),
                }
            )
    return {
        "added_cash_flows": added,
        "cash_flow_duplicate_count": duplicates,
        "cash_flow_problems": [
            problem for entry in history for problem in entry["new_cash_flow_problems"]
        ]
        + problems,
        "new_cash_flow_problems": problems,
    }
