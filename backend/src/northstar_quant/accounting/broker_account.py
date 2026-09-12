"""Value accepted broker executions through the shared account, never synthetic fills.

The caller verifies the baseline, source prefix and catalog identities. This
projection adds no durable account authority and does not establish cash flows,
settlement or the unknown fees missing from CTP trade callbacks.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from northstar_quant.execution.orders import Offset, Side
from northstar_quant.market_data import Instrument

from .amounts import decimal_text
from .fifo import Account
from .fills import FillFact


def project_account(
    baseline: dict[str, Any], history: list[dict[str, Any]], markets: tuple[Instrument, ...]
) -> dict[str, Any]:
    """Return known FIFO P&L with explicit incomplete monetary coverage."""
    if not history or any(item["problems"] for item in history):
        raise ValueError("broker account requires accepted position evidence")
    if baseline["currency"] != "CNY" or baseline["scope"] != "FLAT_CNY_OBSERVATION":
        raise ValueError("broker account requires an established flat CNY baseline")
    account = Account(Decimal(baseline["opening"]["funds"]["Balance"]), markets)
    for entry in history:
        for fill in entry["added_fills"]:
            if fill["trading_day"] != baseline["trading_day"] or fill["fee"] is not None:
                raise ValueError("broker account lacks cross-day or separate fee evidence")
            # External order identity stays external: it is not a local send attempt.
            order_id = ":".join((fill["exchange"], fill["trading_day"], fill["order_sys_id"]))
            account.apply(
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
                    available_at=datetime.fromisoformat(entry["recorded_at"]),
                )
            )
    pending = account.pending_fee_fill_ids
    return {
        "status": "INCOMPLETE",
        "scope": "SAME_DAY_FIFO_FROM_CONFIRMED_BROKER_FILLS",
        "baseline_id": baseline["baseline_id"],
        "through_entry_id": history[-1]["entry_id"],
        "currency": "CNY",
        "realized_pnl_before_fees": decimal_text(account.realized_pnl),
        "cash": None,
        "total_fees": None if pending else "0",
        "pending_fee_fill_ids": list(pending),
        "fill_count": len(account.applied_fills),
        "positions": account.checkpoint()["positions"],
        "reconciliation": "UNRECONCILED",
        "limitations": ["NO_CONFIRMED_FEE_CASHFLOW_OR_SETTLEMENT_COVERAGE"],
        "execution": {"order_sending": False, "cancel_sending": False},
    }
