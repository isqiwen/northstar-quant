"""Bind core metadata/calendar projections to owned immutable response evidence."""

from datetime import date
from typing import Any

from sqlalchemy import Connection, text

from ..files import SourceFiles
from .record_review import EvidenceUnavailable, fixed_rows


def verify(c: Connection, contract: Any, dataset: str, start: date, end: date) -> dict[str, Any]:
    inputs = (
        c.execute(
            text("""SELECT DISTINCT r.*,j.dataset,j.scope,j.parameters,j.start_at,j.end_at
        FROM data_contract_requests cr JOIN data_sync_jobs j USING(request_id)
        JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
        JOIN data_sync_coverage v ON v.request_id=j.request_id AND v.receipt_id=r.receipt_id
        WHERE cr.scope=:owner AND j.scope=:exchange AND j.dataset=:dataset
        AND j.status='VALIDATED' ORDER BY r.receipt_id"""),
            dict(owner=contract["ts_code"], exchange=contract["exchange"], dataset=dataset),
        )
        .mappings()
        .all()
    )
    evidence = dict(
        rule="contract-reference-records/1",
        inputs=[
            dict(receipt_id=str(r["receipt_id"]), manifest_hash=r["manifest_hash"]) for r in inputs
        ],
    )
    if not inputs:
        return dict(
            status="UNKNOWN", reason="核心主数据尚无拥有者关联的固定响应证据", evidence=evidence
        )
    found = False
    days: dict[date, bool] = {}
    try:
        files = SourceFiles.from_environment()
        for item in inputs:
            for row in fixed_rows(files, dict(item)):
                if dataset == "contracts":
                    if row.get("ts_code") != contract["ts_code"]:
                        continue
                    for field in ("list_date", "delist_date", "last_ddate", "d_mode_desc"):
                        if row.get(field) != contract["details"].get(field):
                            raise EvidenceUnavailable(f"合约主数据与固定来源不一致：{field}")
                    if (
                        row.get("exchange") != contract["exchange"]
                        or row.get("fut_code") != contract["product"]
                    ):
                        raise EvidenceUnavailable("合约的交易所/品种与固定来源不一致")
                    found = True
                else:
                    day = date.fromisoformat(str(row["cal_date"]))
                    if not start <= day <= end:
                        continue
                    if row.get("exchange") != contract["exchange"] or str(
                        row.get("is_open")
                    ) not in {"0", "1"}:
                        raise EvidenceUnavailable("固定交易日历的身份或开闭市值无效")
                    value = str(row["is_open"]) == "1"
                    if day in days and days[day] != value:
                        raise EvidenceUnavailable(f"固定交易日历冲突：{day}")
                    days[day] = value
        if dataset == "calendar":
            projection = {
                row.cal_date: row.is_open
                for row in c.execute(
                    text("""SELECT cal_date,is_open FROM data_sync_calendar
                WHERE exchange=:exchange AND cal_date BETWEEN :start AND :end"""),
                    dict(exchange=contract["exchange"], start=start, end=end),
                )
            }
            if len(days) != (end - start).days + 1 or days != projection:
                raise EvidenceUnavailable("交易日历投影与固定来源未逐日一致覆盖生命周期")
            found = True
    except (EvidenceUnavailable, ValueError, KeyError) as error:
        return dict(status="UNKNOWN", reason=f"核心主数据证据未确认：{error}", evidence=evidence)
    return dict(
        status="VERIFIED" if found else "UNKNOWN",
        reason="核心主数据与固定来源已核对" if found else "固定来源未包含该合约",
        evidence=evidence,
    )
