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

RULE = "tushare-response/12"
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


class EmptyResponse(Empty):
    """The provider returned no rows, distinct from incomplete local coverage."""


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


def _observed(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("trade_time", "trade_date", "end_date"):
        value = raw.get(name)
        if isinstance(value, str) and re.fullmatch(r"[0-9 :\-]{8,19}", value):
            result[name] = value
    for name in (*_OHLC, "vol", "amount", "oi", "settle"):
        if name not in raw:
            continue
        value = raw[name]
        if value is None:
            result[name] = None
        elif not isinstance(value, bool) and re.fullmatch(
            r"[+-]?[0-9]{1,30}(?:\.[0-9]{1,18})?(?:[eE][+-]?[0-9]{1,3})?", str(value)
        ):
            result[name] = str(value)
    return result


def normalize(content: bytes, job: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = decode(content)
    definition = BY_KEY[job["dataset"]]
    if len(data["items"]) >= definition.limit:
        raise Truncated("达到接口行数上限，不能认定区间完整")
    if not data["items"]:
        raise EmptyResponse("源端返回空结果，等待发布或覆盖核查；不推进完整覆盖")
    required = (
        (*definition.identity, *_OHLC, "vol")
        if definition.api in _BAR_APIS
        else definition.identity
    )
    if definition.frequency in ("week", "month"):
        required = (*required, "freq")
    if definition.fields:
        required = (*required, *definition.fields)
    missing = sorted(set(required) - set(data["fields"]))
    if missing:
        raise InvalidResponse("响应缺少必需字段：" + ", ".join(missing), fields=tuple(missing))
    rows: dict[tuple[str, ...], dict[str, Any]] = {}
    positions: dict[tuple[str, ...], int] = {}
    issues: list[dict[str, Any]] = []
    failures = 0
    for position, values in enumerate(data["items"], 1):
        try:
            raw = dict(zip(data["fields"], values, strict=True))
            key, row = _row(dict(raw), job)
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
                # Preserve bounded, typed evidence even when rejected raw files are released.
                # Never copy arbitrary supplier text or unrecognized fields into diagnostics.
                issue["observed"] = _observed(raw)
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
    evidence = _evidence(ordered, len(data["items"]) - failures, job["dataset"])
    evidence["excluded_rows"] = failures
    evidence["issues"] = issues
    evidence["issue_count"] = failures
    evidence["truncated"] = failures > 100
    evidence["policy"] = (
        "任一异常行拒绝整份响应；原文行号从1开始，最多显示100项；响应通过不代表合约完整"
    )
    # Exclusion decisions participate in the immutable publication identity.
    evidence["content_hash"] = hashlib.sha256(
        json.dumps(
            {"rows": evidence["content_hash"], "excluded_rows": failures, "issues": issues},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return ordered, evidence


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
        "zero_volume_rows": sum(row.get("observation_status") == "ZERO_VOLUME" for row in rows),
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
            if row["freq"] != definition.frequency:
                raise InvalidResponse("周/月线频率与请求不一致", fields=("freq", "end_date"))
            # Retrospective calculations can have end_date years after the bar.
            # Keep both source dates; this is a range key, NOT first availability.
            day = min(day, cutoff)
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
        zero_volume = row.get("vol") == "0"
        missing_ohl = all(row.get(field) is None for field in ("open", "high", "low"))
        # Observed daily supplier records may retain a reference close on zero-volume
        # days. Preserve nulls; neither that close nor settlement proves an execution.
        reference_only = (
            job["dataset"] in {"daily", "adjusted", "week", "month"}
            and zero_volume
            and missing_ohl
            and row.get("amount") in (None, "0")
        )
        settlement_only = (
            reference_only and row.get("close") is None and row.get("settle") is not None
        )
        checked = ("settle",) if settlement_only else ("close",) if reference_only else _OHLC
        try:
            prices = [number(row[field]) for field in checked]
        except InvalidResponse as error:
            raise InvalidResponse(
                "行情价格缺失或无效；仅零成交日/周/月线可保留空开高低价", fields=checked
            ) from error
        if any(not p.is_finite() or p <= 0 for p in prices):
            raise InvalidResponse(
                "源响应价格为零、负数或非有限值；不能作为成交价格", fields=checked
            )
        if not reference_only:
            o, h, low, c = prices
            if not low <= min(o, c) <= max(o, c) <= h:
                raise InvalidResponse("OHLC 高低价关系不成立", fields=_OHLC)
        if definition.api in _BAR_APIS and row["vol"] is None:
            raise InvalidResponse("行情 vol 缺少成交量；不得填零", fields=("vol",))
        row["observation_status"] = (
            "SETTLEMENT_ONLY" if settlement_only else "ZERO_VOLUME" if zero_volume else "TRADED"
        )
    for field in ("vol", "oi", "amount"):
        if row.get(field) is not None:
            quantity = number(row[field])
            if not quantity.is_finite() or quantity < 0:
                raise InvalidResponse("成交量、持仓量或金额无效", fields=(field,))
    if job["dataset"] == "settlement":
        for field in sorted(normalization.fields("settlement")):
            if row.get(field) is not None:
                value = number(row[field])
                if value < 0 or (field == "settle" and value == 0):
                    raise InvalidResponse("结算价或费率无效", fields=(field,))
    if job["dataset"] == "calendar" and (
        type(row.get("is_open")) is not int or row["is_open"] not in (0, 1)
    ):
        raise InvalidResponse("交易日历开闭市标识无效", fields=("is_open",))
    return key, row
