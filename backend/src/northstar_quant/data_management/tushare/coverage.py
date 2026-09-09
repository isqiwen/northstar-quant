"""Calendar-based daily coverage; absence is never inferred from the last returned row."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Engine, text

from .quality import Empty

_DAILY = {"daily", "settlement", "limits", "mapping", "adjusted"}


def expected_days(engine: Engine, job: dict[str, Any]) -> set[str]:
    with engine.connect() as connection:
        exchange = connection.scalar(
            text("SELECT exchange FROM data_sync_contracts WHERE ts_code=:code"),
            {"code": job["scope"]},
        )
        if exchange is None:
            raise Empty("合约目录尚未就绪，等待核对覆盖范围")
        rows = (
            connection.execute(
                text("""SELECT cal_date,is_open FROM data_sync_calendar
            WHERE exchange=:exchange AND cal_date BETWEEN :start AND :end"""),
                {
                    "exchange": exchange,
                    "start": date.fromisoformat(job["start_at"]),
                    "end": date.fromisoformat(job["end_at"]),
                },
            )
            .mappings()
            .all()
        )
    start, end = date.fromisoformat(job["start_at"]), date.fromisoformat(job["end_at"])
    expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    if {row["cal_date"] for row in rows} != expected:
        raise Empty("交易日历覆盖未就绪，不猜测缺失日期是否休市")
    return {row["cal_date"].strftime("%Y%m%d") for row in rows if row["is_open"]}


def verify(
    engine: Engine, job: dict[str, Any], rows: list[dict[str, Any]], quality: dict[str, Any]
) -> None:
    if job["dataset"] not in _DAILY:
        return
    expected = expected_days(engine, job)
    actual = {row["trade_date"] for row in rows}
    if expected - actual:
        raise Empty(f"交易日内存在缺口（{len(expected - actual)} 日），等待补齐；不推进完整覆盖")
    if actual - expected:
        raise ValueError("返回日线位于日历休市日期")
    quality["coverage_basis"] = "CONTRACT_LIFETIME_AND_TRADING_CALENDAR"
    quality["expected_trading_days"] = len(expected)


def confirmed_empty(engine: Engine, job: dict[str, Any]) -> bool:
    if job["dataset"] not in _DAILY:
        return False
    return not expected_days(engine, job)
