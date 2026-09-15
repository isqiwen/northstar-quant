"""Detect missing trading days without claiming an unverified intraday grid."""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any

from sqlalchemy import Connection, text

from ..files import SourceFiles
from . import minute_policy
from .record_review import EvidenceUnavailable, fixed_rows

REFERENCE = "https://www.shfe.com.cn/docview/docview_35218417.htm"
DCE_REFERENCE = "https://www.dce.com.cn/dalianshangpin/resource/cms/2019/04/2019042612023697006.pdf"


def inspect(
    c: Connection, scope: str, exchange: str, dataset: str, start: date, end: date
) -> dict[str, Any]:
    evidence: dict[str, Any] = dict(
        rule="minute-diagnostics/4",
        grid_verified=False,
        classification="RULE_UNCONFIRMED",
        rules_pending=minute_policy.evidence()["pending"],
        supplier_policy=minute_policy.evidence(),
        volume_basis="SUPPLIER_REPORTED_NOT_SIDE_ADJUSTED",
    )
    unresolved: dict[str, Any] = dict(
        status="RECEIVED",
        reason=(
            "已取得客服结束标签、集合竞价和延长归集规则；"
            "尚缺历史时段及各周期完整标签表，不能判定全部分钟完整"
        ),
        evidence=evidence,
    )
    day_assignment_verified = True
    if exchange == "SHFE" and end < date(2013, 7, 5):
        evidence["reference"] = REFERENCE
    elif exchange == "DCE" and end < date(2014, 7, 1):
        # The exchange's March 2019 report dates its first night products to
        # July 2014. Use the conservative month boundary, not a guessed first day.
        evidence["reference"] = DCE_REFERENCE
    else:
        day_assignment_verified = False
    evidence["trading_day_assignment_verified"] = day_assignment_verified
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
    duplicate_records = 0
    zero_volume_records = 0
    minute_values: dict[str, tuple[Any, ...]] = {}
    minute_volume: dict[date, Decimal] = defaultdict(Decimal)
    daily_volume: dict[date, Decimal] = {}
    conflicts: set[str] = set()
    labels: dict[date, set[str]] = {}
    clock_counts: dict[str, int] = defaultdict(int)
    try:
        files = SourceFiles.from_environment()
        with localcontext() as precision:
            precision.prec = 50
            for item in inputs:
                for row in fixed_rows(files, dict(item)):
                    if row.get("ts_code") != scope:
                        raise EvidenceUnavailable("分钟/日线身份与回执不一致")
                    if item["dataset"] == "daily":
                        day = date.fromisoformat(row["trade_date"])
                        if row.get("vol") is not None and start <= day <= end:
                            volume = Decimal(str(row["vol"]))
                            if day in daily_volume and daily_volume[day] != volume:
                                conflicts.add(day.isoformat())
                            daily_volume[day] = volume
                            if volume > 0:
                                traded.add(day)
                    else:
                        day = datetime.fromisoformat(row["trade_time"]).date()
                        if start <= day <= end:
                            minute_days.add(day)
                            label = row["trade_time"]
                            labels.setdefault(day, set()).add(label)
                            records += 1
                            values = tuple(
                                row.get(f) for f in ("open", "high", "low", "close", "vol")
                            )
                            if label in minute_values:
                                duplicate_records += 1
                                if minute_values[label] != values:
                                    conflicts.add(label)
                                continue
                            minute_values[label] = values
                            clock_counts[datetime.fromisoformat(label).strftime("%H:%M:%S")] += 1
                            volume = Decimal(str(row["vol"]))
                            minute_volume[day] += volume
                            zero_volume_records += volume == 0
    except (ValueError, KeyError, OSError) as error:
        evidence["classification"] = "EVIDENCE_UNAVAILABLE"
        return dict(status="UNKNOWN", reason=f"分钟覆盖证据不可读取：{error}", evidence=evidence)
    missing = sorted(traded - minute_days) if day_assignment_verified else []
    differences = [
        dict(
            date=day.isoformat(),
            minute_volume=_quantity(minute_volume[day]),
            daily_volume=_quantity(daily_volume[day]),
        )
        for day in sorted(minute_days & daily_volume.keys())
        if day_assignment_verified and minute_volume[day] != daily_volume[day] and not conflicts
    ]
    evidence.update(
        observed_records=records,
        duplicate_records=duplicate_records,
        zero_volume_records=zero_volume_records,
        conflicting_records=len(conflicts),
        conflict_samples=sorted(conflicts)[:20],
        volume_difference_count=len(differences),
        volume_differences=differences[:20],
        volume_comparison=(
            "DIAGNOSTIC_ONLY_NOT_A_COMPLETENESS_PROOF"
            if day_assignment_verified
            else "NOT_COMPARED_TRADING_DAY_UNVERIFIED"
        ),
        observed_label_clocks=[
            dict(clock=clock, records=count) for clock, count in sorted(clock_counts.items())
        ],
        daily_traded_days=len(traded),
        minute_days=len(minute_days),
        distinct_records=sum(len(values) for values in labels.values()),
        min_labels_per_day=min((len(values) for values in labels.values()), default=0),
        max_labels_per_day=max((len(values) for values in labels.values()), default=0),
        missing_dates=[d.isoformat() for d in missing[:20]],
    )
    if conflicts:
        evidence["classification"] = "RECORD_CONFLICT"
        return dict(
            status="UNKNOWN",
            reason=(
                f"固定来源记录冲突：{sorted(conflicts)[0]}，共 {len(conflicts)} 个标签；"
                "需核实来源版本，不自动选择或合并"
            ),
            evidence=evidence,
        )
    if missing:
        evidence["classification"] = "RECORD_GAP"
        return dict(
            status="INVALID",
            reason=(
                f"{missing[0]} 日线有成交，但固定 {dataset} 记录缺少整日；"
                f"共缺 {len(missing)} 日，需核查响应及补采"
            ),
            evidence=evidence,
        )
    if differences:
        evidence["classification"] = "RECORD_DIFFERENCE"
        first = differences[0]
        unresolved["reason"] = (
            f"记录差异待归因：{first['date']} 分钟合计 {first['minute_volume']} 手，"
            f"日线 {first['daily_volume']} 手，共 {len(differences)} 日；"
            "不据此缩放、补值或认定源端缺失。" + unresolved["reason"]
        )
    unresolved["reason"] = (
        f"已核对 {len(traded)} 个日线有成交日期，分钟记录 {records} 条；"
        if day_assignment_verified
        else f"已读取 {records} 条分钟记录；历史夜盘归属未核实，未按自然日比较成交量；"
    ) + unresolved["reason"]
    return unresolved


def diagnosis(item: dict[str, Any]) -> dict[str, Any]:
    """Explain the existing admission result without turning observations into permission."""
    evidence = item.get("evidence", {})
    category = evidence.get("classification")
    if category is None:
        category = {
            "INVALID": "RECORD_ERROR",
            "RECEIVED": "RULE_UNCONFIRMED",
            "VERIFIED": "VERIFIED",
        }.get(item["status"], "REQUEST_COVERAGE_PENDING")
    labels = {
        "VERIFIED": "分钟已核验",
        "RECORD_ERROR": "记录异常",
        "RECORD_GAP": "记录缺日",
        "RECORD_DIFFERENCE": "记录差异待归因",
        "RECORD_CONFLICT": "固定来源冲突",
        "REQUEST_COVERAGE_PENDING": "请求覆盖待完成",
        "EVIDENCE_UNAVAILABLE": "核验证据不可读取",
        "RULE_UNCONFIRMED": "历史时段与完整标签待核实",
    }
    actions = {
        "VERIFIED": "按固定发布身份浏览实际记录",
        "RECORD_ERROR": "查看请求诊断中的原始异常，修复可确认的请求问题后定向重采",
        "RECORD_GAP": "按缺失日期核查原始响应与请求边界，再决定定向补采",
        "RECORD_DIFFERENCE": "对照固定分钟和日线原文核实口径；不自动改值或清理",
        "RECORD_CONFLICT": "核实冲突来源版本，保留全部证据",
        "REQUEST_COVERAGE_PENDING": "查看请求诊断，区分未请求、重试、权限和空响应",
        "EVIDENCE_UNAVAILABLE": "恢复或核实固定文件与回执，不能据此认定源端缺失",
        "RULE_UNCONFIRMED": "客服生成规则已收录；补齐品种历史时段与周期标签后核验，不重复全量下载",
    }
    return dict(
        category=category,
        label=labels[category],
        action=actions[category],
        source_missing_confirmed=False,
    )


def _quantity(value: Decimal) -> str:
    fixed = format(value, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
