from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from northstar_quant.application import scheduler as live_scheduler


def test_profile_cron_weekday_uses_standard_cron_numbers_not_apscheduler_offsets():
    trigger = live_scheduler._parse_cron("0 9 * * 1-5", timezone="Asia/Shanghai")
    timezone = ZoneInfo("Asia/Shanghai")
    previous = None
    current = datetime(2026, 1, 4, 0, tzinfo=timezone)  # Sunday
    occurrences = []
    for _ in range(6):
        next_time = trigger.get_next_fire_time(previous, current if previous is None else previous)
        assert next_time is not None
        occurrences.append(next_time)
        previous = next_time

    assert [item.date().isoformat() for item in occurrences] == [
        "2026-01-05",  # Monday
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",  # Friday
        "2026-01-12",  # no Saturday trigger
    ]


def test_profile_cron_weekday_normalization_supports_names_and_sunday_ending_ranges():
    assert live_scheduler._normalize_profile_day_of_week("mon-fri") == "mon-fri"
    assert live_scheduler._normalize_profile_day_of_week("5-7") == "fri-sun"
    assert live_scheduler._normalize_profile_day_of_week("1-7") == "mon-sun"
    with pytest.raises(ValueError, match="跨周范围"):
        live_scheduler._normalize_profile_day_of_week("7-1")


def test_build_and_send_daily_report_skips_info_alert_when_no_anomaly(monkeypatch):
    calls: list[tuple[str, str]] = []
    email_calls: list[tuple[str, str | None]] = []
    report_calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        live_scheduler,
        "load_trading_profile",
        lambda _profile_id: SimpleNamespace(profile_id="cn_futures_daily_live", enabled_strategies=[]),
    )
    monkeypatch.setattr(live_scheduler, "get_production_profile_id", lambda: "cn_futures_daily_live")
    monkeypatch.setattr(
        live_scheduler,
        "build_periodic_report_only",
        lambda report_type, strategy, profile_id: (
            report_calls.append((report_type, strategy, profile_id))
            or "/tmp/daily_report.md"
        ),
    )
    monkeypatch.setattr(
        live_scheduler,
        "build_report_email_subject",
        lambda **_: "Northstar Quant - 日报 - daily_report",
    )
    monkeypatch.setattr(
        live_scheduler,
        "send_report_via_email",
        lambda report_path, subject=None: (
            email_calls.append((report_path, subject))
            or {"sent": True, "report_path": report_path, "subject": subject}
        ),
    )
    monkeypatch.setattr(
        live_scheduler,
        "latest_live_account_attribution_summary",
        lambda profile_id: {"profile_id": profile_id, "alert_lines": []},
    )
    monkeypatch.setattr(live_scheduler, "build_daily_alert_notification", lambda *_: None)
    monkeypatch.setattr(live_scheduler, "send_alert", lambda message, level="info": calls.append((level, message)))

    result = live_scheduler._build_and_send_report("daily")

    assert result["alert_sent"] is False
    assert calls == []
    assert email_calls == [
        ("/tmp/daily_report.md", "Northstar Quant - 日报 - daily_report")
    ]
    assert report_calls == [("daily", "portfolio", "cn_futures_daily_live")]


def test_build_and_send_daily_report_sends_warning_for_anomaly(monkeypatch):
    calls: list[tuple[str, str]] = []
    email_calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        live_scheduler,
        "load_trading_profile",
        lambda profile_id: SimpleNamespace(profile_id=profile_id, enabled_strategies=[]),
    )
    monkeypatch.setattr(live_scheduler, "get_production_profile_id", lambda: "cn_futures_daily_live")
    monkeypatch.setattr(
        live_scheduler,
        "build_periodic_report_only",
        lambda report_type, strategy, profile_id: "/tmp/daily_report.md",
    )
    monkeypatch.setattr(
        live_scheduler,
        "build_report_email_subject",
        lambda **_: "Northstar Quant - 日报 [执行异常] - daily_report",
    )
    monkeypatch.setattr(
        live_scheduler,
        "send_report_via_email",
        lambda report_path, subject=None: (
            email_calls.append((report_path, subject))
            or {"sent": True, "report_path": report_path, "subject": subject}
        ),
    )
    monkeypatch.setattr(
        live_scheduler,
        "latest_live_account_attribution_summary",
        lambda profile_id: {
            "profile_id": profile_id,
            "alert_items": [{"tag": "执行异常", "message": "执行损耗达到 60.00"}],
            "alert_lines": ["[执行异常] 执行损耗达到 60.00"],
        },
    )
    monkeypatch.setattr(
        live_scheduler,
        "build_daily_alert_notification",
        lambda report_path, summary: (
            f"日报检测到异常归因 [执行异常]。\n报告：{report_path}\n"
            f"- [{summary['alert_items'][0]['tag']}] {summary['alert_items'][0]['message']}"
        ),
    )
    monkeypatch.setattr(live_scheduler, "send_alert", lambda message, level="info": calls.append((level, message)))

    result = live_scheduler._build_and_send_report("daily")

    assert result["alert_sent"] is True
    assert email_calls == [
        ("/tmp/daily_report.md", "Northstar Quant - 日报 [执行异常] - daily_report")
    ]
    assert calls == [
        (
            "warning",
            "日报检测到异常归因 [执行异常]。\n报告：/tmp/daily_report.md\n- [执行异常] 执行损耗达到 60.00",
        )
    ]


def test_build_and_send_weekly_report_keeps_info_success_alert(monkeypatch):
    calls: list[tuple[str, str]] = []
    email_calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        live_scheduler,
        "load_trading_profile",
        lambda profile_id: SimpleNamespace(profile_id=profile_id, enabled_strategies=[]),
    )
    monkeypatch.setattr(live_scheduler, "get_production_profile_id", lambda: "cn_futures_daily_live")
    monkeypatch.setattr(
        live_scheduler,
        "build_periodic_report_only",
        lambda report_type, strategy, profile_id: "/tmp/weekly_report.md",
    )
    monkeypatch.setattr(
        live_scheduler,
        "build_report_email_subject",
        lambda **_: "Northstar Quant - 周报 - weekly_report",
    )
    monkeypatch.setattr(
        live_scheduler,
        "send_report_via_email",
        lambda report_path, subject=None: (
            email_calls.append((report_path, subject))
            or {"sent": True, "report_path": report_path, "subject": subject}
        ),
    )
    monkeypatch.setattr(live_scheduler, "send_alert", lambda message, level="info": calls.append((level, message)))

    result = live_scheduler._build_and_send_report("weekly")

    assert result["alert_sent"] is False
    assert email_calls == [
        ("/tmp/weekly_report.md", "Northstar Quant - 周报 - weekly_report")
    ]
    assert calls == [("info", "weekly 报告邮件发送成功：/tmp/weekly_report.md")]


def test_monthly_report_runs_only_on_last_trading_session(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        live_scheduler,
        "_build_and_send_report",
        lambda report_type: calls.append(report_type) or {"report_path": "/tmp/monthly.md"},
    )
    monkeypatch.setattr(live_scheduler, "is_profile_last_trading_day", lambda **_kwargs: False)

    skipped = live_scheduler._build_monthly_report_if_due(
        profile=SimpleNamespace(profile_id="test"),
        broker_name="ctp",
    )

    monkeypatch.setattr(live_scheduler, "is_profile_last_trading_day", lambda **_kwargs: True)
    generated = live_scheduler._build_monthly_report_if_due(
        profile=SimpleNamespace(profile_id="test"),
        broker_name="ctp",
    )

    assert skipped is None
    assert generated == {"report_path": "/tmp/monthly.md"}
    assert calls == ["monthly"]


def test_yearly_report_runs_only_on_last_trading_session(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        live_scheduler,
        "_build_and_send_report",
        lambda report_type: calls.append(report_type)
        or {"report_path": "/tmp/yearly.md"},
    )
    monkeypatch.setattr(live_scheduler, "is_profile_last_trading_day", lambda **_kwargs: False)

    skipped = live_scheduler._build_yearly_report_if_due(
        profile=SimpleNamespace(profile_id="test"),
        broker_name="ctp",
    )

    monkeypatch.setattr(live_scheduler, "is_profile_last_trading_day", lambda **_kwargs: True)
    generated = live_scheduler._build_yearly_report_if_due(
        profile=SimpleNamespace(profile_id="test"),
        broker_name="ctp",
    )

    assert skipped is None
    assert generated == {"report_path": "/tmp/yearly.md"}
    assert calls == ["yearly"]


def test_run_scheduler_registers_shadow_run_job(monkeypatch):
    added_job_ids: list[str] = []
    scheduler_timezones: list[str] = []
    trigger_timezones: list[str] = []
    guard_contexts: list[str] = []

    class _StopScheduler(RuntimeError):
        pass

    class FakeScheduler:
        def __init__(self, timezone=None):
            self.timezone = timezone
            scheduler_timezones.append(timezone)

        def add_job(self, func, trigger, id, replace_existing=True, **_kwargs):
            del func, trigger, replace_existing
            added_job_ids.append(id)

        def start(self):
            raise _StopScheduler()

    monkeypatch.setattr(live_scheduler, "BlockingScheduler", FakeScheduler)
    monkeypatch.setattr(
        live_scheduler,
        "_parse_cron",
        lambda expr, *, timezone=None: (
            trigger_timezones.append(timezone)
            or expr
        ),
    )
    monkeypatch.setattr(
        live_scheduler,
        "_guarded_job",
        lambda job_name, func, *, calendar_guard, **_kwargs: (
            guard_contexts.append(job_name)
            or func
        ),
    )
    monkeypatch.setattr(live_scheduler, "send_alert", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        live_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            scheduler_timezone="America/New_York",
            exchange_calendar="XSHG",
            broker="ctp",
            shadow_run_cron="20 15 * * 1-5",
            daily_signal_cron="20 15 * * 1-5",
            execution_cron="5 9,21 * * 1-5",
            runtime_risk_cron="*/1 * * * 1-5",
            broker_sync_cron="0,15,30,45 9-16 * * 1-5",
            daily_report_cron="45 16 * * 1-5",
            weekly_report_cron="0 17 * * 5",
            monthly_report_cron="0 17 28-31 * *",
            yearly_report_cron="15 17 * 12 *",
        ),
    )
    monkeypatch.setattr(
        live_scheduler,
        "load_trading_profile",
        lambda profile_id: SimpleNamespace(
            profile_id=profile_id,
            timezone="America/New_York",
            calendar="XNYS",
            enabled_strategies=[],
            schedule={},
        ),
    )
    monkeypatch.setattr(live_scheduler, "get_production_profile_id", lambda: "cn_futures_daily_live")

    with pytest.raises(_StopScheduler):
        live_scheduler.run_scheduler()

    assert "daily_shadow_run" in added_job_ids
    assert "daily_signal" in added_job_ids
    assert "daily_execution" in added_job_ids
    assert "runtime_risk" in added_job_ids
    assert "yearly_report" in added_job_ids
    assert scheduler_timezones == ["America/New_York"]
    assert trigger_timezones == ["America/New_York"] * 9
    assert guard_contexts == [
        "daily_signal",
        "daily_shadow_run",
        "daily_execution",
    ]


def test_guarded_job_skips_new_risk_work_when_calendar_is_unknown(monkeypatch):
    calls: list[tuple[str, str]] = []
    ran: list[bool] = []
    monkeypatch.setattr(
        live_scheduler,
        "send_alert",
        lambda message, level="info": calls.append((level, message)),
    )

    guarded = live_scheduler._guarded_job(
        "daily_execution",
        lambda: ran.append(True),
        calendar_guard=lambda: (_ for _ in ()).throw(
            live_scheduler.CalendarGateError("TRADING_CALENDAR_SCHEDULER_BLOCKED: unknown")
        ),
    )

    assert guarded() is None
    assert ran == []
    assert calls == [
        ("info", "跳过任务 daily_execution：TRADING_CALENDAR_SCHEDULER_BLOCKED: unknown")
    ]
