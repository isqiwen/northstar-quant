"""交易日历工具。

统一封装交易日过滤逻辑，避免调度器在非交易日误触发实盘。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from northstar_quant.config.settings import get_settings

try:
    import exchange_calendars as xcals
except Exception:  # pragma: no cover
    xcals = None


def now_local(timezone: str | None = None) -> datetime:
    """返回指定交易画像时区下的当前时间。"""

    settings = get_settings()
    timezone_name = timezone or settings.scheduler_timezone
    return datetime.now(ZoneInfo(timezone_name))


def is_trading_session(
    dt: datetime | None = None,
    *,
    calendar: str | None = None,
    timezone: str | None = None,
    require_calendar: bool = False,
) -> bool:
    """判断给定时间所在日期是否为交易日。

    这里只做“交易日”过滤，不做盘中分钟级门禁。
    对日频系统来说，这已经能拦住绝大多数误执行。
    """

    settings = get_settings()
    timezone_name = timezone or settings.scheduler_timezone
    calendar_name = calendar or settings.exchange_calendar
    if dt is None:
        local_dt = now_local(timezone_name)
    elif dt.tzinfo is None:
        local_dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        local_dt = dt.astimezone(ZoneInfo(timezone_name))
    if xcals is None:
        if require_calendar:
            raise RuntimeError(
                "交易日历依赖不可用，真实券商模式禁止退化为工作日判断。"
            )
        return local_dt.weekday() < 5

    try:
        cal = xcals.get_calendar(calendar_name)
    except Exception as exc:
        if require_calendar:
            raise RuntimeError(
                f"无法加载交易日历 {calendar_name}，真实券商模式禁止继续。"
            ) from exc
        raise
    session_label = local_dt.date()
    return bool(cal.is_session(session_label))


def is_last_trading_session_of_month(
    dt: datetime | None = None,
    *,
    calendar: str | None = None,
    timezone: str | None = None,
    require_calendar: bool = True,
) -> bool:
    """判断当前日期是否为该日历当月最后一个交易日。"""

    settings = get_settings()
    timezone_name = timezone or settings.scheduler_timezone
    calendar_name = calendar or settings.exchange_calendar
    if dt is None:
        local_dt = now_local(timezone_name)
    elif dt.tzinfo is None:
        local_dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        local_dt = dt.astimezone(ZoneInfo(timezone_name))

    if xcals is None:
        if require_calendar:
            raise RuntimeError("交易日历依赖不可用，无法判断月末交易日。")
        if local_dt.weekday() >= 5:
            return False
        next_day = local_dt + timedelta(days=1)
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)
        return next_day.month != local_dt.month

    try:
        cal = xcals.get_calendar(calendar_name)
        session_label = local_dt.date()
        if not cal.is_session(session_label):
            return False
        next_session = cal.next_session(session_label)
    except Exception as exc:
        if require_calendar:
            raise RuntimeError(
                f"无法使用交易日历 {calendar_name} 判断月末交易日。"
            ) from exc
        raise
    return int(next_session.month) != local_dt.month
