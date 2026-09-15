"""Retain a complete native settlement document without guessing its money format.

Request/terminal correlation is owned by query_projection. This module verifies
its fragments and produces the same immutable document on cold reconstruction.
A returned document is evidence, not a settlement confirmation or ledger posting.
"""

import base64
import binascii
import hashlib
from datetime import date
from typing import Any


def validate_day(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("settlement day must be YYYY-MM-DD")
    return value


def assemble(
    section: dict[str, Any], *, day: str, broker_id: str, account_id: str
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "INCOMPLETE",
        "trading_day": day,
        "content": None,
        "content_sha256": None,
        "encoding": "GBK",
        "problems": [],
        "ledger_posted": False,
        "confirmation_sent": False,
    }
    if section["status"] != "COMPLETE":
        result["problems"].append("SETTLEMENT_QUERY_NOT_COMPLETE")
        return result
    rows = section["rows"]
    if not rows:
        result.update(status="NOT_RETURNED", problems=["SETTLEMENT_NOT_RETURNED"])
        return result
    fragments: list[bytes] = []
    settlement_id: int | None = None
    previous: int | None = None
    for row in rows:
        if (
            row.get("BrokerID") != broker_id
            or row.get("InvestorID") != account_id
            or row.get("AccountID") not in {None, "", account_id}
            or row.get("CurrencyID") != "CNY"
            or row.get("TradingDay") != day.replace("-", "")
        ):
            result["problems"].append("SETTLEMENT_SCOPE_MISMATCH")
            break
        current_id, sequence = row.get("SettlementID"), row.get("SequenceNo")
        if (
            type(current_id) is not int
            or current_id < 0
            or settlement_id is not None
            and current_id != settlement_id
            or type(sequence) is not int
            or sequence < 0
            or previous is None
            and sequence not in {0, 1}
            or previous is not None
            and sequence != previous + 1
        ):
            result["problems"].append("SETTLEMENT_FRAGMENT_SEQUENCE_UNKNOWN")
            break
        settlement_id, previous = current_id, sequence
        try:
            encoded = row["ContentBase64"]
            if not isinstance(encoded, str):
                raise ValueError("missing fragment")
            fragment = base64.b64decode(encoded, validate=True)
            if len(fragment) > 500 or base64.b64encode(fragment).decode("ascii") != encoded:
                raise ValueError("invalid fragment")
            fragments.append(fragment)
        except (KeyError, ValueError, binascii.Error):
            result["problems"].append("SETTLEMENT_FRAGMENT_INVALID")
            break
    if result["problems"]:
        return result
    raw = b"".join(fragments)
    try:
        content = raw.decode("gbk", errors="strict")
        if not content.strip() or "\x00" in content:
            raise ValueError("empty or malformed content")
    except (UnicodeError, ValueError):
        result["problems"].append("SETTLEMENT_CONTENT_INVALID")
        return result
    result.update(
        status="RECEIVED",
        content=content,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        settlement_id=settlement_id,
        fragment_count=len(fragments),
        byte_count=len(raw),
    )
    return result
