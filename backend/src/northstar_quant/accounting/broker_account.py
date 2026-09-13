"""Value accepted broker executions through the shared account, never synthetic fills.

The caller verifies the baseline, source prefix and catalog identities. This
projection adds no durable account authority. Identified cash movements do not
establish complete transfer coverage, settlement or unknown CTP execution fees.
"""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from northstar_quant.execution.orders import Offset, Side

from .amounts import decimal_text
from .cashflows import CashFlowFact
from .fifo import Account
from .fills import FillFact
from .journal import AccountJournalError


def entry_facts(entry: dict[str, Any], *, trading_day: str) -> tuple[FillFact | CashFlowFact, ...]:
    facts: list[FillFact | CashFlowFact] = []
    at = datetime.fromisoformat(entry["recorded_at"])
    for fill in entry["added_fills"]:
        if (
            not isinstance(fill["contract_id"], str)
            or fill["hedge_flag"] != "1"
            or fill["trading_day"] != trading_day
        ):
            raise ValueError("broker monetary fact lacks its supported contract/day/hedge scope")
        if fill["fee"] is not None:
            raise ValueError("broker fees require separately identified coverage")
        order_id = ":".join((fill["exchange"], fill["trading_day"], fill["order_sys_id"]))
        facts.append(
            FillFact(
                fill["fill_id"],
                order_id,
                UUID(fill["contract_id"]),
                None,
                datetime.fromisoformat(fill["filled_at"]),
                date.fromisoformat(fill["trading_day"]),
                Side(fill["direction"]),
                Offset(fill["offset"]),
                fill["quantity_lots"],
                Decimal(fill["price"]),
                None,
                available_at=at,
            )
        )
    for row in entry["added_cash_flows"]:
        facts.append(replace(CashFlowFact.from_dict(row), available_at=at))
    return tuple(facts)


def project_account(
    baseline: dict[str, Any],
    history: list[dict[str, Any]],
    account: Account,
) -> dict[str, Any]:
    """Return known FIFO P&L with explicit incomplete monetary coverage."""
    if not history or any(item["position_projection"]["status"] != "KNOWN" for item in history):
        raise ValueError("broker account requires accepted position evidence")
    if baseline["currency"] != "CNY" or baseline["scope"] != "FLAT_CNY_OBSERVATION":
        raise ValueError("broker account requires an established flat CNY baseline")
    if account.initial_cash != Decimal(baseline["opening"]["funds"]["Balance"]):
        raise AccountJournalError("account journal opening differs from its verified baseline")
    pending = account.pending_fee_fill_ids
    return {
        "status": "INCOMPLETE",
        "scope": "SAME_DAY_FIFO_FROM_CONFIRMED_BROKER_FILLS",
        "baseline_id": baseline["baseline_id"],
        "through_entry_id": history[-1]["entry_id"],
        "currency": "CNY",
        "realized_pnl_before_fees": decimal_text(account.realized_pnl),
        "cash": None,
        "total_fees": None if pending else decimal_text(account.total_fees),
        "pending_fee_fill_ids": list(pending),
        "fill_count": len(account.applied_fills),
        "net_identified_cash_flow": decimal_text(account.net_cash_flow),
        "cash_flow_count": account.checkpoint()["cash_flow_count"],
        "cash_flow_problems": history[-1]["cash_flow_problems"],
        "positions": account.checkpoint()["positions"],
        "reconciliation": "UNRECONCILED",
        "limitations": ["NO_CONFIRMED_FEE_CASHFLOW_OR_SETTLEMENT_COVERAGE"],
        "execution": {"order_sending": False, "cancel_sending": False},
    }
