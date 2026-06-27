"""日频调度器。"""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.live.service import run_live_once, run_shadow_once, sync_broker_once
from northstar_quant.live.trading_calendar import is_trading_session
from northstar_quant.logging_.logger import get_logger
from northstar_quant.monitoring.alerts import send_alert
from northstar_quant.reporting.email_sender import send_report_via_email
from northstar_quant.reporting.report_builder import (
    build_daily_alert_notification,
    build_report_email_subject,
    build_periodic_report_only,
    latest_live_account_attribution_summary,
)

logger = get_logger(__name__, command="live.scheduler")


def _parse_cron(expr: str) -> CronTrigger:
    """把五段 cron 表达式转成 APScheduler 触发器。"""

    minute, hour, day, month, day_of_week = expr.split()
    settings = get_settings()
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=settings.scheduler_timezone,
    )


def _guarded_job(job_name: str, func):
    """包装任务，在非交易日直接跳过。"""

    def _wrapped():
        job_logger = logger.bind(job_name=job_name)
        if not is_trading_session():
            job_logger.info("调度任务被跳过，原因=非交易日")
            send_alert(f"跳过任务 {job_name}：今天不是交易日。", level="info")
            return None
        job_logger.info("开始执行调度任务")
        return func()

    return _wrapped


def _build_and_send_report(report_type: str) -> dict:
    """生成周期报告，并在配置了 SMTP 时自动发送邮件。"""

    profile = load_trading_profile()
    report_logger = logger.bind(
        job_name=f"{report_type}_report",
        report_type=report_type,
        profile=profile.profile_id,
    )
    first_strategy = profile.enabled_strategies[0].strategy_id if profile.enabled_strategies else "etf_rotation"
    report_path = build_periodic_report_only(
        report_type,
        strategy=first_strategy,
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


def run_scheduler() -> None:
    """启动阻塞式日频调度器。"""

    settings = get_settings()
    profile = load_trading_profile()
    schedule = profile.schedule
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    logger.bind(profile=profile.profile_id).info("开始初始化调度器")

    scheduler.add_job(
        _guarded_job("broker_sync", sync_broker_once),
        _parse_cron(settings.broker_sync_cron),
        id="broker_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        _guarded_job("daily_shadow_run", lambda: run_shadow_once(profile.profile_id)),
        _parse_cron(schedule.get("shadow_run_cron", settings.shadow_run_cron)),
        id="daily_shadow_run",
        replace_existing=True,
    )
    scheduler.add_job(
        _guarded_job("daily_rebalance", lambda: run_live_once(profile.profile_id)),
        _parse_cron(schedule.get("rebalance_cron", settings.rebalance_cron)),
        id="daily_rebalance",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _build_and_send_report("daily"),
        _parse_cron(schedule.get("daily_report_cron", settings.daily_report_cron)),
        id="daily_report",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _build_and_send_report("weekly"),
        _parse_cron(schedule.get("weekly_report_cron", settings.weekly_report_cron)),
        id="weekly_report",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _build_and_send_report("monthly"),
        _parse_cron(schedule.get("monthly_report_cron", settings.monthly_report_cron)),
        id="monthly_report",
        replace_existing=True,
    )

    send_alert("Northstar Quant 日频调度器已启动。", level="info")
    logger.bind(profile=profile.profile_id).info("调度器已启动并完成任务注册")
    scheduler.start()
