"""Validate supplier rows without inventing trades, sessions or missing observations."""

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from . import normalization
from .acquisition import decode
from .catalog import BY_KEY

RULE = "tushare-response/4"
_OHLC = ("open", "high", "low", "close")
# These APIs declare OHLC and volume; ancillary amount/oi may remain unknown.
# Official Tushare doc_id: 313, 138, 337, 492, 468 (reviewed 2026-09-10).
_BAR_APIS = normalization.BAR_APIS


class InvalidResponse(ValueError):
    """Bounded diagnostics reference raw row positions, never echo supplier values."""

    def __init__(
        self,
        message: str,
        *,
        fields: tuple[str, ...] = (),
        issues: list[dict[str, Any]] | None = None,
        count: int = 1,
    ):
        super().__init__(message)
        self.report: dict[str, Any] = {
            "rule": RULE,
            "issue_count": count,
            "issues": issues
            if issues is not None
            else [{"row_number": None, "fields": list(fields), "reason": message}],
            "truncated": count > 100,
            "policy": "每行记录首个失败；最多显示100项，行号从原始响应第一行开始计数",
        }


class Truncated(ValueError):
    pass


class Empty(ValueError):
    pass


def number(value: Any) -> Decimal:
    """Malformed supplier numerics are quality failures, not worker crashes."""
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise InvalidResponse("供应商数值字段无效") from error


def _date(value: Any) -> datetime:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise InvalidResponse("供应商日期必须为 YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise InvalidResponse("供应商日期无效") from error


def _minute(value: Any) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:00", value) is None
    ):
        raise InvalidResponse("分钟 trade_time 必须为完整分钟时间 YYYY-MM-DD HH:MM:00")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as error:
        raise InvalidResponse("分钟 trade_time 无效") from error


def normalize(content: bytes, job: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = decode(content)
    definition = BY_KEY[job["dataset"]]
    if len(data["items"]) >= definition.limit:
        raise Truncated("达到接口行数上限，不能认定区间完整")
    if not data["items"]:
        raise Empty("源端返回空结果，等待发布或覆盖核查；不推进完整覆盖")
    required = (
        (*definition.identity, *_OHLC, "vol")
        if definition.api in _BAR_APIS
        else definition.identity
    )
    if definition.frequency in ("week", "month"):
        required = (*required, "freq")
    missing = sorted(set(required) - set(data["fields"]))
    if missing:
        raise InvalidResponse("响应缺少必需字段：" + ", ".join(missing), fields=tuple(missing))
    rows: dict[tuple[str, ...], dict[str, Any]] = {}
    positions: dict[tuple[str, ...], int] = {}
    issues: list[dict[str, Any]] = []
    failures = 0
    for position, values in enumerate(data["items"], 1):
        try:
            key, row = _row(dict(zip(data["fields"], values, strict=True)), job)
            if key in rows and rows[key] != row:
                conflict = InvalidResponse(
                    "同一记录身份返回冲突内容；隔离该区间", fields=definition.identity
                )
                conflict.report["issues"][0]["related_row_number"] = positions[key]
                raise conflict
            rows[key] = row
            positions.setdefault(key, position)
        except InvalidResponse as error:
            failures += 1
            if len(issues) < 100:
                issue = {**error.report["issues"][0], "row_number": position}
                issues.append(issue)
    if failures:
        raise InvalidResponse(issues[0]["reason"], issues=issues, count=failures)
    if job["dataset"] == "calendar":
        from datetime import timedelta

        start = datetime.fromisoformat(job["start_at"]).date()
        end = datetime.fromisoformat(job["end_at"]).date()
        expected = {
            (start + timedelta(days=i)).strftime("%Y%m%d") for i in range((end - start).days + 1)
        }
        if {row["cal_date"] for row in rows.values()} != expected:
            raise Empty("交易日历缺少日期；等待完整日历，不猜测休市")
    ordered = [rows[key] for key in sorted(rows)]
    return ordered, _evidence(ordered, len(data["items"]), job["dataset"])


def closed_interval_evidence(dataset: str) -> dict[str, Any]:
    """Called only after the worker verifies a complete non-trading calendar interval."""
    return _evidence([], 0, dataset, closed=True)


def _evidence(
    rows: list[dict[str, Any]], total: int, dataset: str, *, closed: bool = False
) -> dict[str, Any]:
    # A new rule must retain new evidence even when its accepted rows are unchanged.
    canonical = json.dumps(
        {
            "rule": RULE,
            "normalization": normalization.evidence(dataset),
            "rows": rows,
            "calendar_closed": closed,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return {
        "rule": RULE,
        "normalization": normalization.evidence(dataset),
        "unique_rows": len(rows),
        "duplicate_rows": total - len(rows),
        "content_hash": hashlib.sha256(canonical).hexdigest(),
        "coverage_basis": "CALENDAR_NON_TRADING" if closed else "SUPPLIER_RESPONSE",
        "availability_basis": "FINAL_REVISED",
        "note": "已核对完整交易日历；该区间没有交易日，未填造行情。"
        if closed
        else "已检查响应字段、范围、重复和量价；不以行数证明源端全部分钟无遗漏。",
    }


def _row(row: dict[str, Any], job: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, Any]]:
    definition = BY_KEY[job["dataset"]]
    key = tuple(str(row[field]) for field in definition.identity)
    if any(row[field] is None or row[field] == "" for field in definition.identity):
        raise InvalidResponse("数据身份字段为空", fields=definition.identity)
    parameters = job["parameters"]
    for field in ("ts_code", "exchange"):
        if field in parameters and field in row and row[field] != parameters[field]:
            raise InvalidResponse("返回的合约或交易所不属于请求范围", fields=(field,))
    clock = row.get("trade_time", row.get("cal_date", row.get("trade_date")))
    if clock is not None:
        clock_field = (
            "trade_time"
            if "trade_time" in row
            else "cal_date"
            if "cal_date" in row
            else "trade_date"
        )
        try:
            day = (_minute(clock) if "trade_time" in row else _date(clock)).date()
        except InvalidResponse as error:
            raise InvalidResponse(str(error), fields=(clock_field,)) from error
        # Week/month bars can be stamped at the future period end; end_date
        # preserves the actual calculation cutoff instead of treating it as available.
        if definition.frequency in ("week", "month"):
            cutoff = _date(row["end_date"]).date()
            if cutoff > day or row["freq"] != definition.frequency:
                raise InvalidResponse(
                    "周/月线频率或计算截至日期与期末标签不一致", fields=("freq", "end_date")
                )
            day = cutoff
        if job["start_at"] and not job["start_at"] <= day.isoformat() <= job["end_at"]:
            raise InvalidResponse("行情时间超出请求窗口", fields=(clock_field,))
    try:
        normalization.normalize(row, job["dataset"])
    except ValueError as error:
        raise InvalidResponse(
            str(error),
            fields=tuple(
                f for f in normalization.fields(job["dataset"]) if str(error).startswith(f + "：")
            ),
        ) from error
    for name, value in list(row.items()):
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise InvalidResponse("数值不是有限十进制值")
            try:
                row[name] = normalization.decimal_text(value)
            except ValueError as error:
                raise InvalidResponse("额外数值字段超出精确范围") from error
    if definition.api in _BAR_APIS or all(field in row for field in _OHLC):
        try:
            prices = [number(row[field]) for field in _OHLC]
        except InvalidResponse as error:
            raise InvalidResponse(str(error), fields=_OHLC) from error
        if any(not p.is_finite() or p < 0 for p in prices):
            raise InvalidResponse("OHLC 价格无效", fields=_OHLC)
        o, h, low, c = prices
        if not low <= min(o, c) <= max(o, c) <= h:
            raise InvalidResponse("OHLC 高低价关系不成立", fields=_OHLC)
        if definition.api in _BAR_APIS and row["vol"] is None:
            raise InvalidResponse("行情 vol 缺少成交量；不得填零", fields=("vol",))
    for field in ("vol", "oi", "amount"):
        if row.get(field) is not None:
            quantity = number(row[field])
            if not quantity.is_finite() or quantity < 0:
                raise InvalidResponse("成交量、持仓量或金额无效", fields=(field,))
    if job["dataset"] == "calendar" and (
        type(row.get("is_open")) is not int or row["is_open"] not in (0, 1)
    ):
        raise InvalidResponse("交易日历开闭市标识无效", fields=("is_open",))
    return key, row
