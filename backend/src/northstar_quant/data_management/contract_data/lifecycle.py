"""Actual supplier lifecycle facts, independent of collection/publication state."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


def local_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def metadata_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 8 or not value.isascii() or not value.isdigit():
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


@dataclass(frozen=True)
class Lifetime:
    start: date
    end: date  # Last trading day: price observations stop here.
    last_delivery: date  # Business completion is a separate admission boundary.


def describe(contract: Any, *, today: date | None = None) -> dict[str, Any]:
    today = today or local_today()
    details = contract["details"]
    start = metadata_date(details.get("list_date"))
    trade = metadata_date(details.get("delist_date"))
    delivery = metadata_date(details.get("last_ddate"))
    # fut_basic does not provide a first delivery date. Do not infer it from codes.
    state, reason = "UNKNOWN", "上市、最后交易或最后交割日期缺失/无效，等待元数据核实"
    if contract["kind"] != "1":
        state, reason = "NOT_REAL", "连续序列不是可独立发布的真实合约"
    elif start and trade and delivery:
        if not start <= trade <= delivery:
            reason = "上市、最后交易与最后交割日期顺序冲突，等待元数据核实"
        elif start > today:
            state, reason = "NOT_LISTED", "尚未上市"
        elif trade >= today:
            state, reason = "TRADING", "尚未完成最后交易日"
        elif delivery >= today:
            state, reason = "DELIVERING", "交易已结束，最后交割日尚未完成"
        else:
            state, reason = "ENDED", "最后交易日和最后交割日均已完成"
    return dict(
        listing_date=start.isoformat() if start else None,
        last_trade_date=trade.isoformat() if trade else None,
        first_delivery_date=None,
        last_delivery_date=delivery.isoformat() if delivery else None,
        delivery_month=details.get("d_month") or None,
        lifecycle_status=state,
        lifecycle_reason=reason,
    )


def completed(contract: Any, *, today: date | None = None) -> Lifetime:
    facts = describe(contract, today=today)
    if facts["lifecycle_status"] != "ENDED":
        raise ValueError(facts["lifecycle_reason"])
    return Lifetime(
        date.fromisoformat(facts["listing_date"]),
        date.fromisoformat(facts["last_trade_date"]),
        date.fromisoformat(facts["last_delivery_date"]),
    )
