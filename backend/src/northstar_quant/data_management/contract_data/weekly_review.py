"""Verify native product reports by reported dates, not guessed ISO identifiers."""

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, text

from ..files import SourceFiles
from .record_review import EvidenceUnavailable, fixed_rows, period

RULE = "product-weekly-records/1"


def verify(
    c: Connection, owner: str, exchange: str, product: str, start: date, end: date
) -> dict[str, Any]:
    calendar = c.execute(
        text("""SELECT cal_date,is_open FROM data_sync_calendar WHERE exchange=:exchange
        AND cal_date BETWEEN :start AND :end ORDER BY cal_date"""),
        dict(exchange=exchange, start=start, end=end),
    ).all()
    evidence: dict[str, Any] = dict(rule=RULE, execution_admission=False)
    if len(calendar) != (end - start).days + 1:
        return dict(status="UNKNOWN", reason="交易日历未覆盖逐自然日范围", evidence=evidence)
    expected = {period(day, "week") for day, is_open in calendar if is_open}
    last_trading = {
        key: max(day for day, is_open in calendar if is_open and period(day, "week") == key)
        for key in expected
    }
    if not expected:
        return dict(status="UNKNOWN", reason="没有可核对的交易周", evidence=evidence)
    inputs = (
        c.execute(
            text("""SELECT DISTINCT r.*,j.dataset,j.scope,j.parameters,j.start_at,j.end_at
        FROM data_contract_requests cr JOIN data_sync_jobs j USING(request_id)
        JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
        JOIN data_sync_coverage v ON v.request_id=j.request_id AND v.receipt_id=r.receipt_id
        WHERE cr.scope=:owner AND j.scope=:scope AND j.dataset='weekly_detail'
        AND j.status='VALIDATED' ORDER BY r.receipt_id"""),
            dict(owner=owner, scope=f"{exchange}:{product}"),
        )
        .mappings()
        .all()
    )
    evidence.update(
        calendar_hash=hashlib.sha256(
            json.dumps([(day.isoformat(), is_open) for day, is_open in calendar]).encode()
        ).hexdigest(),
        inputs=[
            dict(receipt_id=str(r["receipt_id"]), manifest_hash=r["manifest_hash"]) for r in inputs
        ],
        expected_periods=len(expected),
        actual_periods=0,
        missing_periods=[],
    )
    reports: dict[date, dict[str, Any]] = {}
    undated: set[str] = set()
    try:
        files = SourceFiles.from_environment()
        for item in inputs:
            for row in fixed_rows(files, dict(item)):
                if row.get("exchange") != exchange or row.get("prd") != product:
                    raise ValueError("品种周报的交易所/品种身份不匹配")
                if not row.get("week_date"):
                    undated.add(str(row.get("week")))
                    continue
                label = date.fromisoformat(str(row["week_date"]))
                key = period(label, "week")
                if key not in expected:
                    # Annual source envelopes also contain other contracts' periods.
                    continue
                if label < last_trading[key]:
                    raise ValueError(f"周报日期 {label} 尚未覆盖本周应有区间")
                for field in ("vol", "amount", "open_interest", "cumvol", "cumamt"):
                    if row.get(field) is None:
                        raise ValueError(f"{label} 缺少 {field}")
                    value = Decimal(str(row.get(field)))
                    if not value.is_finite() or value < 0:
                        raise ValueError(f"{label} {field} 无效")
                previous = reports.get(key)
                if previous is not None and previous != row:
                    raise ValueError(f"{label} 同一交易周存在冲突的品种报告")
                reports[key] = row
    except EvidenceUnavailable as error:
        return dict(status="UNKNOWN", reason=f"固定周报证据不可读取：{error}", evidence=evidence)
    except (ValueError, KeyError, InvalidOperation) as error:
        return dict(status="INVALID", reason=f"固定周报核验失败：{error}", evidence=evidence)
    missing = sorted(expected - reports.keys())
    evidence.update(
        actual_periods=len(reports),
        missing_periods=[d.isoformat() for d in missing[:20]],
        undated_native_periods=sorted(undated)[:20],
    )
    if undated:
        return dict(
            status="UNKNOWN",
            evidence=evidence,
            reason=f"原生周报 {', '.join(sorted(undated)[:5])} 缺少 week_date；"
            "不能按 ISO 周猜测日期，须核实其归属及受影响区间",
        )
    if missing:
        return dict(
            status="INVALID",
            evidence=evidence,
            reason=f"实际品种周报缺少 {len(missing)} 个有交易的周，首个 {missing[0]}；"
            "须核查请求边界与源端披露，不能用连续请求窗口代替记录",
        )
    return dict(
        status="VERIFIED",
        evidence=evidence,
        reason=f"按原生 week_date 核对 {len(reports)} 个交易周，身份、记录及必需数值完整；"
        "原始周编号保留，不推断首次可得时间",
    )
