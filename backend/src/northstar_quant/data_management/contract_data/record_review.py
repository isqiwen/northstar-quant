"""Check retained contract rows against the complete, current exchange calendar.

Supplier labels stay supplier labels. This proves daily/native-period coverage,
not historical first availability, execution fee units, or minute session grids.
"""

import calendar
import hashlib
import io
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, text

from ..files import SourceFiles
from .requirements import auxiliary_fields

RULE = "contract-records/3"


class EvidenceUnavailable(ValueError):
    """Storage/receipt disagreement is not a provider data rejection."""


SUPPORTED = frozenset({"daily", "settlement", "limits", "week", "month"})


def verify(
    c: Connection, scope: str, exchange: str, dataset: str, start: date, end: date
) -> dict[str, Any]:
    calendar_rows = c.execute(
        text("""SELECT cal_date,is_open FROM data_sync_calendar WHERE exchange=:exchange
        AND cal_date BETWEEN :start AND :end ORDER BY cal_date"""),
        dict(exchange=exchange, start=start, end=end),
    ).all()
    if len(calendar_rows) != (end - start).days + 1:
        return dict(
            status="UNKNOWN", reason="交易日历没有覆盖逐自然日范围", evidence=dict(rule=RULE)
        )
    expected = {day for day, is_open in calendar_rows if is_open}
    if not expected:
        return dict(status="UNKNOWN", reason="没有可核对的交易日历", evidence=dict(rule=RULE))
    periods = {period(day, dataset) for day in expected}
    inputs = (
        c.execute(
            text("""SELECT DISTINCT r.*,j.dataset,j.scope,j.parameters,j.start_at,j.end_at
        FROM data_contract_requests cr JOIN data_sync_jobs j USING(request_id)
        JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
        JOIN data_sync_coverage v ON v.request_id=j.request_id AND v.receipt_id=r.receipt_id
        WHERE cr.scope=:scope AND j.scope=:scope AND j.dataset=:dataset
        AND j.status='VALIDATED' ORDER BY r.receipt_id"""),
            dict(scope=scope, dataset=dataset),
        )
        .mappings()
        .all()
    )
    evidence: dict[str, Any] = dict(
        rule=RULE,
        calendar_hash=hashlib.sha256(
            json.dumps([(day.isoformat(), is_open) for day, is_open in calendar_rows]).encode()
        ).hexdigest(),
        expected_records=len(periods),
        actual_records=0,
        missing_dates=[],
        inputs=[
            dict(receipt_id=str(r["receipt_id"]), manifest_hash=r["manifest_hash"]) for r in inputs
        ],
        execution_admission=False,
    )
    rows: dict[date, dict[str, Any]] = {}
    unknown_margin_dates: set[date] = set()
    files = SourceFiles.from_environment()
    try:
        for item in inputs:
            for row in fixed_rows(files, dict(item)):
                if row.get("ts_code") != scope:
                    raise ValueError("固定记录的合约身份不匹配")
                label = date.fromisoformat(str(row["trade_date"]))
                key = period(label, dataset)
                if key not in periods:
                    raise ValueError(f"{label} 不属于本合约应有交易日或原生周期")
                if dataset in {"week", "month"}:
                    if row.get("freq") != dataset:
                        raise ValueError(f"{label} 原生周期频率不匹配")
                    cutoff = date.fromisoformat(str(row["end_date"]))
                    required = max(day for day in expected if period(day, dataset) == key)
                    if cutoff < required or label < required:
                        raise ValueError(
                            f"{label} 计算截至 {cutoff}，未覆盖应有最后交易日 {required}"
                        )
                _fields(row, dataset, label)
                if dataset == "limits" and row.get("m_ratio") is None:
                    unknown_margin_dates.add(label)
                previous = rows.get(key)
                if previous is not None and previous != row:
                    raise ValueError(f"{label} 多份固定响应含冲突记录")
                rows[key] = row
        missing = sorted(periods - rows.keys())
        optional = auxiliary_fields(dataset)
        gaps = {
            field: {
                "count": sum(row.get(field) is None for row in rows.values()),
                "dates": [
                    day.isoformat() for day, row in sorted(rows.items()) if row.get(field) is None
                ][:20],
            }
            for field in sorted(optional)
            if any(row.get(field) is None for row in rows.values())
        }
        evidence.update(
            actual_records=len(rows),
            missing_dates=[d.isoformat() for d in missing[:20]],
            optional_unknown_fields=gaps,
        )
        if missing:
            return dict(
                status="INVALID",
                reason=(
                    f"实际记录缺少 {len(missing)} 个交易日/周期，首个 {missing[0]}；"
                    "请求覆盖不代表记录齐全"
                ),
                evidence=evidence,
            )
    except EvidenceUnavailable as error:
        return dict(status="UNKNOWN", reason=f"固定证据不可读取：{error}", evidence=evidence)
    except (ValueError, KeyError) as error:
        return dict(status="INVALID", reason=f"固定记录核验失败：{error}", evidence=evidence)
    return dict(
        status="VERIFIED",
        reason=(
            f"已逐条核对 {len(rows)} 个交易日/原生周期，日期齐全、身份及必需字段有效；"
            + (
                f"最低保证金率 {len(unknown_margin_dates)} 日未知，未填充；"
                "每日多空保证金率由结算参数单独必检；"
                if unknown_margin_dates
                else ""
            )
            + "不代表回测准入"
        ),
        evidence=evidence,
    )


def period(day: date, dataset: str) -> date:
    if dataset == "week":
        return day + timedelta(days=4 - day.weekday())
    if dataset == "month":
        return day.replace(day=calendar.monthrange(day.year, day.month)[1])
    return day


def fixed_rows(files: SourceFiles, item: dict[str, Any]) -> Any:
    try:
        yield from _read(files, item)
    except (ValueError, KeyError, OSError) as error:
        raise EvidenceUnavailable(str(error)) from error


def _read(files: SourceFiles, item: dict[str, Any]) -> Any:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    manifest = json.loads(files.read(item["manifest_hash"], item["manifest_bytes"]))
    for key in ("dataset", "scope", "parameters", "start_at", "end_at", "quality"):
        if manifest[key] != item[key]:
            raise ValueError(f"响应清单与固定回执不一致：{key}")
    if manifest["parquet"] != dict(
        content_hash=item["parquet_hash"], byte_count=item["parquet_bytes"]
    ):
        raise ValueError("响应清单与固定行情文件不一致")
    if manifest["source"] != dict(
        content_hash=item["source_hash"], byte_count=item["source_bytes"]
    ):
        raise ValueError("响应清单与原始来源身份不一致")
    table = pq.ParquetFile(io.BytesIO(files.read(item["parquet_hash"], item["parquet_bytes"])))
    if table.metadata.num_rows != item["row_count"]:
        raise ValueError("固定文件行数与回执不一致")
    for batch in table.iter_batches(batch_size=512):
        yield from batch.to_pylist()


def _fields(row: dict[str, Any], dataset: str, label: date) -> None:
    def number(field: str, *, positive: bool = False) -> Decimal:
        raw = row.get(field)
        if raw is None:
            raise ValueError(f"{label} 缺少 {field}")
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            raise ValueError(f"{label} {field} 不是有效数值") from None
        if not value.is_finite() or value < 0 or (positive and value == 0):
            raise ValueError(f"{label} {field} 无效")
        return value

    if dataset == "settlement":
        number("settle", positive=True)
        number("long_margin_rate", positive=True)
        number("short_margin_rate", positive=True)
        if row.get("trading_fee") is None and row.get("trading_fee_rate") is None:
            raise ValueError(f"{label} 缺少手续费金额和费率")
        for field in ("trading_fee", "trading_fee_rate"):
            if row.get(field) is not None:
                number(field)
    elif dataset == "limits":
        if number("up_limit", positive=True) < number("down_limit", positive=True):
            raise ValueError(f"{label} 涨跌停价格倒置")
        if row.get("m_ratio") is not None:
            number("m_ratio", positive=True)
    else:
        volume = number("vol")
        if volume == 0:
            if row.get("close") is None:
                number("settle", positive=True)
            else:
                number("close", positive=True)
            return
        high, low = number("high", positive=True), number("low", positive=True)
        if (
            not low <= number("open", positive=True) <= high
            or not low <= number("close", positive=True) <= high
        ):
            raise ValueError(f"{label} OHLC 价格关系无效")
