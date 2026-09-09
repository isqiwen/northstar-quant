"""Coverage evidence links back to downloader facts, never infers intraday completeness."""

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from ..tushare.store import serial
from .catalog import interval


def coverage(engine: Engine, dataset: str, scope: str, start: str, end: str) -> dict[str, Any]:
    left, right = interval(dataset, scope, start, end)
    with engine.connect() as c:
        contract = (
            c.execute(
                text("SELECT * FROM data_sync_contracts WHERE ts_code=:scope"), {"scope": scope}
            )
            .mappings()
            .one_or_none()
        )
        if contract is None:
            raise LookupError("合约目录中不存在该合约")
        calendar: dict[date, bool] = {
            r[0]: r[1]
            for r in c.execute(
                text("""SELECT cal_date,is_open FROM data_sync_calendar
            WHERE exchange=:exchange AND cal_date BETWEEN :start AND :end"""),
                {"exchange": contract["exchange"], "start": left, "end": right},
            ).all()
        }
        jobs = [
            serial(r)
            for r in c.execute(
                text("""SELECT j.*,r.quality,
            c.receipt_id AS covered_receipt FROM data_sync_jobs j
            LEFT JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
            LEFT JOIN data_sync_coverage c ON c.request_id=j.request_id
            WHERE j.dataset=:dataset AND j.scope=:scope AND j.start_at<=:end AND j.end_at>=:start
            AND j.status<>'SPLIT' ORDER BY j.start_at,j.request_id LIMIT 513"""),
                dict(dataset=dataset, scope=scope, start=start, end=end),
            ).mappings()
        ]
    if len(jobs) > 512:
        raise ValueError("覆盖任务超过 512 个，请缩小范围")
    lifetime = contract["details"]

    def bound(key: str) -> date | None:
        value = lifetime.get(key)
        return datetime.strptime(value, "%Y%m%d").date() if value else None

    listed, expired = bound("list_date"), bound("delist_date")
    days = []
    for i in range((right - left).days + 1):
        day = left + timedelta(days=i)
        windows = [j for j in jobs if j["start_at"] <= day.isoformat() <= j["end_at"]]
        state = "NOT_DOWNLOADED"
        if windows:
            states = {j["status"] for j in windows}
            state = next(
                (s for s in ("BLOCKED", "WAITING", "RUNNING", "PENDING") if s in states),
                "RESPONSE_VALIDATED",
            )
            if state == "RESPONSE_VALIDATED":
                if not all(
                    j["covered_receipt"] == j["receipt_id"] and j["receipt_id"] for j in windows
                ):
                    state = "UNVERIFIED"
                elif dataset in ("daily", "adjusted") and all(
                    (j["quality"] or {}).get("coverage_basis")
                    in ("CONTRACT_LIFETIME_AND_TRADING_CALENDAR", "CALENDAR_NON_TRADING")
                    for j in windows
                ):
                    state = "VERIFIED"
        if (listed and day < listed) or (expired and day > expired):
            state = "NOT_APPLICABLE"
        elif calendar.get(day) is False and dataset in ("daily", "adjusted"):
            state = "CLOSED"
        days.append(
            {
                "date": day.isoformat(),
                "state": state,
                "calendar_open": calendar.get(day),
                "request_ids": [j["request_id"] for j in windows],
            }
        )
    return {
        "days": days,
        "jobs": jobs,
        "note": (
            "分钟状态按供应商自然时间窗口展示；交易日历不代表夜盘时段，响应校验不等于分钟完整。"
        ),
    }
