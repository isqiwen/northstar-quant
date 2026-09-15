"""Detect missing trading days without claiming an unverified intraday grid."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from ..files import SourceFiles
from .record_review import EvidenceUnavailable, fixed_rows

REFERENCE = "https://www.shfe.com.cn/docview/docview_35218417.htm"
DCE_REFERENCE = "https://www.dce.com.cn/dalianshangpin/resource/cms/2019/04/2019042612023697006.pdf"


def inspect(
    c: Connection, scope: str, exchange: str, dataset: str, start: date, end: date
) -> dict[str, Any]:
    evidence: dict[str, Any] = dict(rule="day-session-presence/2", grid_verified=False)
    unresolved: dict[str, Any] = dict(
        status="RECEIVED",
        reason="尚缺供应商历史分钟标签、无成交分钟政策和逐时段规则；不能判定全部分钟完整",
        evidence=evidence,
    )
    if exchange == "SHFE" and end < date(2013, 7, 5):
        evidence["reference"] = REFERENCE
    elif exchange == "DCE" and end < date(2014, 7, 1):
        # The exchange's March 2019 report dates its first night products to
        # July 2014. Use the conservative month boundary, not a guessed first day.
        evidence["reference"] = DCE_REFERENCE
    else:
        return unresolved
    inputs = (
        c.execute(
            text("""SELECT DISTINCT r.*,j.dataset,j.scope,j.parameters,j.start_at,j.end_at
        FROM data_contract_requests cr JOIN data_sync_jobs j USING(request_id)
        JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
        JOIN data_sync_coverage v ON v.request_id=j.request_id AND v.receipt_id=r.receipt_id
        WHERE cr.scope=:scope AND j.scope=:scope AND j.dataset IN (:dataset,'daily')
        AND j.status='VALIDATED' ORDER BY r.receipt_id"""),
            dict(scope=scope, dataset=dataset),
        )
        .mappings()
        .all()
    )
    evidence["inputs"] = [
        dict(receipt_id=str(r["receipt_id"]), manifest_hash=r["manifest_hash"]) for r in inputs
    ]
    traded: set[date] = set()
    minute_days: set[date] = set()
    records = 0
    labels: dict[date, set[str]] = {}
    try:
        files = SourceFiles.from_environment()
        for item in inputs:
            for row in fixed_rows(files, dict(item)):
                if row.get("ts_code") != scope:
                    raise EvidenceUnavailable("分钟/日线身份与回执不一致")
                if item["dataset"] == "daily":
                    day = date.fromisoformat(row["trade_date"])
                    if (
                        row.get("vol") is not None
                        and Decimal(str(row["vol"])) > 0
                        and start <= day <= end
                    ):
                        traded.add(day)
                else:
                    day = datetime.fromisoformat(row["trade_time"]).date()
                    if start <= day <= end:
                        minute_days.add(day)
                        labels.setdefault(day, set()).add(row["trade_time"])
                        records += 1
    except (ValueError, KeyError, OSError) as error:
        return dict(status="UNKNOWN", reason=f"分钟覆盖证据不可读取：{error}", evidence=evidence)
    missing = sorted(traded - minute_days)
    evidence.update(
        observed_records=records,
        daily_traded_days=len(traded),
        minute_days=len(minute_days),
        distinct_records=sum(len(values) for values in labels.values()),
        min_labels_per_day=min((len(values) for values in labels.values()), default=0),
        max_labels_per_day=max((len(values) for values in labels.values()), default=0),
        missing_dates=[d.isoformat() for d in missing[:20]],
    )
    if missing:
        return dict(
            status="INVALID",
            reason=(
                f"{missing[0]} 日线有成交，但固定 {dataset} 记录缺少整日；"
                f"共缺 {len(missing)} 日，需核查响应及补采"
            ),
            evidence=evidence,
        )
    unresolved["reason"] = (
        f"已核对 {len(traded)} 个日线有成交日期，分钟记录 {records} 条；" + unresolved["reason"]
    )
    return unresolved
