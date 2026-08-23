"""日频调度器。"""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.config.trading_profile import get_production_profile_id, load_trading_profile
from northstar_quant.foundation.scheduling import JobRegistry, ScheduledJob, ScheduledJobKind
from northstar_quant.application.live_service import (
    execute_latest_targets_once,
    run_runtime_risk_monitor_once,
    run_shadow_once,
    sync_broker_once,
)
from northstar_quant.application.target_service import generate_daily_targets_once
from northstar_quant.application.calendar_gate import (
    CalendarGateError,
    assert_profile_calendar_market_session,
    assert_profile_calendar_trading_day,
    is_profile_last_trading_day,
)
from northstar_quant.foundation.observability.logging.logger import get_logger
from northstar_quant.foundation.observability.monitoring.alerts import send_alert
from northstar_quant.foundation.reporting.email_sender import send_report_via_email
from northstar_quant.application.reporting import (
    build_daily_alert_notification,
    build_report_email_subject,
    build_periodic_report_only,
    latest_live_account_attribution_summary,
)

logger = get_logger(__name__, command="live.scheduler")

_PROFILE_CRON_DAY_NAMES = {
    0: "sun",
    1: "mon",
    2: "tue",
    3: "wed",
    4: "thu",
    5: "fri",
    6: "sat",
    7: "sun",
}
_PROFILE_CRON_NAMED_DAY_NUMBERS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def _parse_cron(expr: str, *, timezone: str | None = None) -> CronTrigger:
    """把五段标准 cron 表达式转成 APScheduler 触发器。

    项目配置沿用常见 cron 语义：星期日为 ``0``/``7``，星期一到星期六为 ``1`` 到 ``6``。
    APScheduler 自己以 ``0=Monday`` 编号，若直接透传会漏掉周一、额外触发周六；这里统一
    转为命名 weekday 后再交给 APScheduler。交易日历仍是最终业务门禁，cron 仅是候选触发。
    """

    minute, hour, day, month, day_of_week = expr.split()
    timezone_name = timezone or get_settings().scheduler_timezone
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=_normalize_profile_day_of_week(day_of_week),
        timezone=timezone_name,
    )


def _normalize_profile_day_of_week(value: str) -> str:
    """将标准 cron 的数值 weekday 转成 APScheduler 无歧义的命名 weekday。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("cron day_of_week 必须是非空字符串")

    normalized_parts: list[str] = []
    for item in value.strip().lower().split(","):
        if not item:
            raise ValueError("cron day_of_week 不能包含空列表项")
        base, slash, step = item.partition("/")
        if slash and (not step or "/" in step):
            raise ValueError("cron day_of_week 的步长格式无效")
        normalized_base = _normalize_profile_day_base(base)
        normalized_parts.append(
            normalized_base if not slash else f"{normalized_base}/{step}"
        )
    return ",".join(normalized_parts)


def _normalize_profile_day_base(value: str) -> str:
    if value == "*":
        return value
    if "-" in value:
        start, separator, end = value.partition("-")
        if not separator or not start or not end or "-" in end:
            raise ValueError("cron day_of_week 范围格式无效")
        start_name = _profile_day_name(start)
        end_name = _profile_day_name(end)
        if _profile_day_range_order(start) > _profile_day_range_order(end):
            raise ValueError("cron day_of_week 不支持跨周范围；请拆成多个列表项")
        # 标准 cron 的 0-6/0-7 覆盖整周；APScheduler 以 Monday 开始编号，`sun-sat`
        # 是逆序范围，直接透传会被误解或拒绝，故显式归一为通配符。
        if {start_name, end_name} == {"sun", "sat"}:
            return "*"
        return f"{start_name}-{end_name}"
    return _profile_day_name(value)


def _profile_day_name(value: str) -> str:
    if value.isdigit():
        return _PROFILE_CRON_DAY_NAMES[_profile_day_number(value)]
    if value in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
        return value
    raise ValueError("cron day_of_week 必须是 0-7、星期英文缩写、范围或列表")


def _profile_day_number(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError("cron day_of_week 范围端点必须是 0-7") from exc
    if number not in _PROFILE_CRON_DAY_NAMES:
        raise ValueError("cron day_of_week 数值必须在 0-7")
    return number


def _profile_day_range_order(value: str) -> int:
    """返回标准 cron 的范围顺序；数值 7 保留为“周末尾端”的 Sunday。"""

    if value.isdigit():
        return _profile_day_number(value)
    try:
        return _PROFILE_CRON_NAMED_DAY_NUMBERS[value]
    except KeyError as exc:
        raise ValueError("cron day_of_week 范围端点必须是数值或星期英文缩写") from exc


def _guarded_job(
    job_name: str,
    func,
    *,
    calendar_guard,
    notify_on_skip: bool = True,
):
    """包装会产生新风险的任务，在日历不明确时跳过。"""

    def _wrapped():
        job_logger = logger.bind(job_name=job_name)
        try:
            calendar_guard()
        except CalendarGateError as exc:
            reason = str(exc)
            job_logger.info("调度任务被跳过，原因=%s", reason)
            if notify_on_skip:
                send_alert(f"跳过任务 {job_name}：{reason}", level="info")
            return None
        job_logger.info("开始执行调度任务")
        return func()

    return _wrapped


def _build_and_send_report(report_type: str) -> dict:
    """生成周期报告，并在配置了 SMTP 时自动发送邮件。"""

    profile = load_trading_profile(get_production_profile_id())
    report_logger = logger.bind(
        job_name=f"{report_type}_report",
        report_type=report_type,
        profile=profile.profile_id,
    )
    report_path = build_periodic_report_only(
        report_type,
        strategy="portfolio",
        profile_id=profile.profile_id,
    )
    report_logger.info("周期报告生成完成，report_path=%s", report_path)
    alert_message = None
    live_account_attribution = None
    if report_type == "daily":
        live_account_attribution = latest_live_account_attribution_summary(
            profile_id=profile.profile_id
        )
    subject = build_report_email_subject(
        report_type=report_type,
        report_path=report_path,
        live_account_attribution=live_account_attribution,
    )
    email_result = send_report_via_email(report_path, subject=subject)
    if report_type == "daily":
        alert_message = build_daily_alert_notification(
            report_path,
            live_account_attribution,
        )
        if alert_message:
            send_alert(alert_message, level="warning")
    elif email_result.get("sent"):
        send_alert(f"{report_type} 报告邮件发送成功：{report_path}", level="info")
    report_logger.info("周期报告处理完成，sent=%s", email_result.get("sent", False))
    return {"report_path": report_path, "email": email_result, "alert_sent": bool(alert_message)}


def _build_monthly_report_if_due(
    *,
    profile,
    broker_name: str,
) -> dict | None:
    """仅在可验证日历的当月最后交易日生成和发送月报。"""

    try:
        due = is_profile_last_trading_day(
            profile=profile,
            broker_name=broker_name,
            period="month",
        )
    except CalendarGateError as exc:
        logger.bind(job_name="monthly_report").info(
            "月报任务被跳过，原因=%s",
            exc,
        )
        return None
    if not due:
        logger.bind(job_name="monthly_report").info(
            "月报任务被跳过，原因=不是当月最后交易日"
        )
        return None
    return _build_and_send_report("monthly")


def _build_yearly_report_if_due(
    *,
    profile,
    broker_name: str,
) -> dict | None:
    """仅在可验证日历的当年最后交易日生成和发送年报。"""

    try:
        due = is_profile_last_trading_day(
            profile=profile,
            broker_name=broker_name,
            period="year",
        )
    except CalendarGateError as exc:
        logger.bind(job_name="yearly_report").info(
            "年报任务被跳过，原因=%s",
            exc,
        )
        return None
    if not due:
        logger.bind(job_name="yearly_report").info(
            "年报任务被跳过，原因=不是当年最后交易日"
        )
        return None
    return _build_and_send_report("yearly")


def run_scheduler() -> None:
    """启动阻塞式日频调度器。"""

    settings = get_settings()
    profile = load_trading_profile(get_production_profile_id())
    schedule = profile.schedule
    profile_timezone = (
        getattr(profile, "timezone", None)
        or getattr(settings, "scheduler_timezone", "Asia/Shanghai")
    )
    broker_name = str(getattr(settings, "broker", "")).strip().lower()

    def trading_day_guard() -> None:
        assert_profile_calendar_trading_day(
            profile=profile,
            broker_name=broker_name,
        )

    def execution_session_guard() -> None:
        assert_profile_calendar_market_session(
            profile=profile,
            broker_name=broker_name,
        )

    scheduler = BlockingScheduler(timezone=profile_timezone)
    job_registry = JobRegistry()
    logger.bind(profile=profile.profile_id).info("开始初始化调度器")

    def register_job(
        *,
        job_id: str,
        kind: ScheduledJobKind,
        action,
        trigger: CronTrigger,
        lifecycle_gate=None,
        **scheduler_options,
    ) -> None:
        job = job_registry.register(
            ScheduledJob(
                job_id=job_id,
                kind=kind,
                action=action,
                lifecycle_gate=lifecycle_gate,
            )
        )
        scheduler.add_job(job.run, trigger, id=job.job_id, replace_existing=True, **scheduler_options)

    register_job(
        job_id="broker_sync",
        kind=ScheduledJobKind.MAINTENANCE,
        action=lambda: sync_broker_once(profile.profile_id),
        trigger=_parse_cron(settings.broker_sync_cron, timezone=profile_timezone),
    )
    register_job(
        job_id="daily_signal",
        kind=ScheduledJobKind.RESEARCH,
        action=_guarded_job(
            "daily_signal", lambda: generate_daily_targets_once(profile.profile_id), calendar_guard=trading_day_guard
        ),
        trigger=_parse_cron(schedule.get("daily_signal_cron", settings.daily_signal_cron), timezone=profile_timezone),
        max_instances=1,
        coalesce=True,
    )
    register_job(
        job_id="daily_shadow_run",
        kind=ScheduledJobKind.RESEARCH,
        action=_guarded_job(
            "daily_shadow_run", lambda: run_shadow_once(profile.profile_id), calendar_guard=trading_day_guard
        ),
        trigger=_parse_cron(schedule.get("shadow_run_cron", settings.shadow_run_cron), timezone=profile_timezone),
        max_instances=1,
        coalesce=True,
    )
    register_job(
        job_id="daily_execution",
        kind=ScheduledJobKind.LIVE,
        action=lambda: execute_latest_targets_once(profile.profile_id),
        lifecycle_gate=execution_session_guard,
        trigger=_parse_cron(schedule.get("execution_cron", settings.execution_cron), timezone=profile_timezone),
        max_instances=1,
        coalesce=True,
    )
    register_job(
        job_id="runtime_risk",
        kind=ScheduledJobKind.MAINTENANCE,
        action=lambda: run_runtime_risk_monitor_once(profile.profile_id),
        trigger=_parse_cron(schedule.get("runtime_risk_cron", settings.runtime_risk_cron), timezone=profile_timezone),
        max_instances=1,
        coalesce=True,
    )
    register_job(
        job_id="daily_report",
        kind=ScheduledJobKind.MAINTENANCE,
        action=lambda: _build_and_send_report("daily"),
        trigger=_parse_cron(schedule.get("daily_report_cron", settings.daily_report_cron), timezone=profile_timezone),
    )
    register_job(
        job_id="weekly_report",
        kind=ScheduledJobKind.MAINTENANCE,
        action=lambda: _build_and_send_report("weekly"),
        trigger=_parse_cron(schedule.get("weekly_report_cron", settings.weekly_report_cron), timezone=profile_timezone),
    )
    register_job(
        job_id="monthly_report",
        kind=ScheduledJobKind.MAINTENANCE,
        action=lambda: _build_monthly_report_if_due(profile=profile, broker_name=broker_name),
        trigger=_parse_cron(schedule.get("monthly_report_cron", settings.monthly_report_cron), timezone=profile_timezone),
    )
    register_job(
        job_id="yearly_report",
        kind=ScheduledJobKind.MAINTENANCE,
        action=lambda: _build_yearly_report_if_due(profile=profile, broker_name=broker_name),
        trigger=_parse_cron(schedule.get("yearly_report_cron", settings.yearly_report_cron), timezone=profile_timezone),
    )

    send_alert("Northstar Quant 日频调度器已启动。", level="info")
    logger.bind(profile=profile.profile_id).info("调度器已启动并完成任务注册")
    scheduler.start()
