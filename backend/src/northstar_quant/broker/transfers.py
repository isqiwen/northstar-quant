"""Translate confirmed CTP transfer receipts, never account-query differences.

Only fee-free normal transfers and exactly linked same-day reversals are valued.
Unknown charges, identity, status or chronology remain unresolved source evidence.
"""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from northstar_quant.accounting.cashflows import CashFlowFact

from .account_reports import _money
from .events import TRANSFER_CALLBACKS


def _identity(broker: str, account: str, day: str, serial: object) -> str:
    if type(serial) is not int or serial <= 0:
        raise ValueError("transfer requires a positive futures-company serial")
    return hashlib.sha256(json.dumps([broker, account, day, serial]).encode()).hexdigest()


def decode_transfer(
    event: dict[str, Any],
    *,
    broker_id: str,
    account_id: str,
    trading_day: str,
    source_reference: str,
    opening_at: datetime,
) -> CashFlowFact:
    day = date.fromisoformat(trading_day)
    row = event.get("data") or {}
    callback = event["callback"]
    if (
        callback not in TRANSFER_CALLBACKS
        or event["channel"] != "TD"
        or event["error_id"]
        or row.get("BrokerID") != broker_id
        or row.get("AccountID") != account_id
        or row.get("TradingDay") != day.strftime("%Y%m%d")
        or row.get("CurrencyID") != "CNY"
        or type(row.get("ErrorID")) is not int
        or row["ErrorID"] != 0
    ):
        raise ValueError("transfer receipt has no confirmed account, day, currency or success")
    reversal = "Repeal" in callback
    if reversal and (
        row.get("BrokerRepealFlag") != "2" or row.get("BankRepealFlag") not in {"0", "2"}
    ):
        raise ValueError("reversal is not confirmed by the futures company and bank")
    if row.get("TransferStatus") != ("1" if reversal else "0"):
        raise ValueError("transfer status does not confirm its callback effect")
    if any(Decimal(_money(row.get(key))) != 0 for key in ("CustFee", "BrokerFee")):
        raise ValueError("transfer charges require separate confirmed accounting evidence")
    amount = Decimal(_money(row.get("TradeAmount")))
    if amount <= 0:
        raise ValueError("transfer receipt requires a positive nominal amount")
    transferred = (
        datetime.strptime(f"{row.get('TradeDate')} {row.get('TradeTime')}", "%Y%m%d %H:%M:%S")
        .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        .astimezone(UTC)
    )
    if transferred.astimezone(ZoneInfo("Asia/Shanghai")).date() > day:
        raise ValueError("transfer date exceeds its confirmed trading day")
    if transferred <= opening_at:
        raise ValueError("transfer may already be included in the opening account observation")
    available = datetime.fromisoformat(event["received_at"])
    incoming = "FromBankToFuture" in callback
    if incoming == reversal:
        amount = amount.copy_negate()
    return CashFlowFact(
        _identity(broker_id, account_id, day.isoformat(), row.get("FutureSerial")),
        amount,
        "CNY",
        transferred,
        available,
        source_reference,
        _identity(broker_id, account_id, day.isoformat(), row.get("FutureRepealSerial"))
        if reversal
        else None,
    )
