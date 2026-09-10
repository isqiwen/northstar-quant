"""Validate supplier rows without inventing trades, sessions or missing observations."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .acquisition import decode
from .catalog import BY_KEY


class Truncated(ValueError):
    pass


class Empty(ValueError):
    pass


def number(value: Any) -> Decimal:
    """Malformed supplier numerics are quality failures, not worker crashes."""
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("供应商数值字段无效") from error


def normalize(content: bytes, job: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = decode(content)
    definition = BY_KEY[job["dataset"]]
    if len(data["items"]) >= definition.limit:
        raise Truncated("达到接口行数上限，不能认定区间完整")
    if not data["items"]:
        raise Empty("源端返回空结果，等待发布或覆盖核查；不推进完整覆盖")
    if not set(definition.identity) <= set(data["fields"]):
        raise ValueError("响应缺少数据身份字段")
    rows: dict[tuple[str, ...], dict[str, Any]] = {}
    for values in data["items"]:
        row = dict(zip(data["fields"], values, strict=True))
        key = tuple(str(row[field]) for field in definition.identity)
        if any(row[field] is None or row[field] == "" for field in definition.identity):
            raise ValueError("数据身份字段为空")
        parameters = job["parameters"]
        for field in ("ts_code", "exchange"):
            if field in parameters and field in row and row[field] != parameters[field]:
                raise ValueError("返回的合约或交易所不属于请求范围")
        clock = row.get("trade_time", row.get("cal_date", row.get("trade_date")))
        if clock is not None:
            day = (
                datetime.strptime(str(clock)[:10], "%Y-%m-%d").date()
                if "-" in str(clock)
                else datetime.strptime(str(clock), "%Y%m%d").date()
            )
            # Week/month bars can be stamped at the future period end; end_date
            # preserves the actual calculation cutoff instead of treating it as available.
            if definition.frequency in ("week", "month"):
                day = datetime.strptime(str(row["end_date"]), "%Y%m%d").date()
            if job["start_at"] and not job["start_at"] <= day.isoformat() <= job["end_at"]:
                raise ValueError("行情时间超出请求窗口")
        for name, value in list(row.items()):
            if isinstance(value, Decimal):
                if not value.is_finite():
                    raise ValueError("数值不是有限十进制值")
                row[name] = format(value, "f")
        if all(field in row for field in ("open", "high", "low", "close")):
            prices = [number(row[field]) for field in ("open", "high", "low", "close")]
            if any(not p.is_finite() or p < 0 for p in prices):
                raise ValueError("OHLC 价格无效")
            o, h, low, c = prices
            if not low <= min(o, c) <= max(o, c) <= h:
                raise ValueError("OHLC 高低价关系不成立")
        for field in ("vol", "oi", "amount"):
            if row.get(field) is not None:
                quantity = number(row[field])
                if not quantity.is_finite() or quantity < 0:
                    raise ValueError("成交量、持仓量或金额无效")
        if row.get("amount") is not None:
            row["amount_cny"] = format(number(row["amount"]) * definition.amount_multiplier, "f")
        if key in rows and rows[key] != row:
            raise ValueError("同一记录身份返回冲突内容；隔离该区间")
        rows[key] = row
    if job["dataset"] == "calendar":
        from datetime import timedelta

        start = datetime.fromisoformat(job["start_at"]).date()
        end = datetime.fromisoformat(job["end_at"]).date()
        expected = {
            (start + timedelta(days=i)).strftime("%Y%m%d") for i in range((end - start).days + 1)
        }
        if {row["cal_date"] for row in rows.values()} != expected:
            raise Empty("交易日历缺少日期；等待完整日历，不猜测休市")
        if any(
            type(row.get("is_open")) is not int or row["is_open"] not in (0, 1)
            for row in rows.values()
        ):
            raise ValueError("交易日历开闭市标识无效")
    ordered = [rows[key] for key in sorted(rows)]
    canonical = json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    quality = {
        "rule": "tushare-response/1",
        "unique_rows": len(ordered),
        "duplicate_rows": len(data["items"]) - len(ordered),
        "content_hash": hashlib.sha256(canonical).hexdigest(),
        "coverage_basis": "SUPPLIER_RESPONSE",
        "availability_basis": "FINAL_REVISED",
        "note": "已检查响应字段、范围、重复和量价；不以行数证明源端全部分钟无遗漏。",
    }
    return ordered, quality
