"""Only a retired real contract can become a collection/publication unit."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Lifetime:
    start: date
    end: date


def completed(contract: Any, *, today: date | None = None) -> Lifetime:
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if contract["kind"] != "1":
        raise ValueError("连续序列不是可独立发布的真实合约")
    try:
        details = contract["details"]
        start_text, end_text = details["list_date"], details["delist_date"]
        if len(start_text) != 8 or len(end_text) != 8:
            raise ValueError("invalid dates")
        start = datetime.strptime(start_text, "%Y%m%d").date()
        end = datetime.strptime(end_text, "%Y%m%d").date()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("上市或退市日期缺失/无效，不能纳入下载") from error
    if start > end:
        raise ValueError("上市日期晚于退市日期")
    if end >= today:
        raise ValueError("尚未完成退市，不下载或发布")
    return Lifetime(start, end)
