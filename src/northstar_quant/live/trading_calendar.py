"""交易日历工具。

统一封装交易日过滤逻辑，避免调度器在非交易日误触发实盘。
"""

from __future__ import annotations

from datetime import datetime

from zoneinfo import ZoneInfo

from northstar_quant.config.settings import get_settings

try:
    import exchange_calendars as xcals
except Exception:  # pragma: no cover
    xcals = None


def now_local() -> datetime:
    """返回调度时区下的当前时间。"""

    settings = get_settings()
    return datetime.now(ZoneInfo(settings.scheduler_timezone))


def is_trading_session(dt: datetime | None = None) -> bool:
    """判断给定时间所在日期是否为交易日。

    这里只做“交易日”过滤，不做盘中分钟级门禁。
    对日频系统来说，这已经能拦住绝大多数误执行。
    """

    settings = get_settings()
    dt = dt or now_local()
    if xcals is None:
        return dt.weekday() < 5

    cal = xcals.get_calendar(settings.exchange_calendar)
    session_label = dt.date()
    return bool(cal.is_session(session_label))
