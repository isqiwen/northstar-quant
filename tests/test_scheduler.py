from types import SimpleNamespace

import pytest

from northstar_quant.live import scheduler as live_scheduler


def test_build_and_send_daily_report_skips_info_alert_when_no_anomaly(monkeypatch):
    calls: list[tuple[str, str]] = []
    email_calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        live_scheduler,
        "load_trading_profile",
        lambda: SimpleNamespace(profile_id="us_etf_daily", enabled_strategies=[]),
    )
    monkeypatch.setattr(
        live_scheduler,
        "build_periodic_report_only",
        lambda report_type, strategy, profile_id: "/tmp/daily_report.md",
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


def test_build_and_send_daily_report_sends_warning_for_anomaly(monkeypatch):
    calls: list[tuple[str, str]] = []
    email_calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        live_scheduler,
        "load_trading_profile",
        lambda: SimpleNamespace(profile_id="us_etf_daily", enabled_strategies=[]),
    )
    monkeypatch.setattr(
        live_scheduler,
        "build_periodic_report_only",
        lambda report_type, strategy, profile_id: "/tmp/daily_report.md",
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
        lambda: SimpleNamespace(profile_id="us_etf_daily", enabled_strategies=[]),
    )
    monkeypatch.setattr(
        live_scheduler,
        "build_periodic_report_only",
        lambda report_type, strategy, profile_id: "/tmp/weekly_report.md",
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


def test_run_scheduler_registers_shadow_run_job(monkeypatch):
    added_job_ids: list[str] = []

    class _StopScheduler(RuntimeError):
        pass

    class FakeScheduler:
        def __init__(self, timezone=None):
            self.timezone = timezone

        def add_job(self, func, trigger, id, replace_existing=True):
            del func, trigger, replace_existing
            added_job_ids.append(id)

        def start(self):
            raise _StopScheduler()

    monkeypatch.setattr(live_scheduler, "BlockingScheduler", FakeScheduler)
    monkeypatch.setattr(live_scheduler, "_parse_cron", lambda expr: expr)
    monkeypatch.setattr(live_scheduler, "send_alert", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        live_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            scheduler_timezone="America/New_York",
            shadow_run_cron="20 15 * * 1-5",
            broker_sync_cron="0,15,30,45 9-16 * * 1-5",
            rebalance_cron="35 15 * * 1-5",
            daily_report_cron="45 16 * * 1-5",
            weekly_report_cron="0 17 * * 5",
            monthly_report_cron="0 17 28-31 * *",
        ),
    )
    monkeypatch.setattr(
        live_scheduler,
        "load_trading_profile",
        lambda: SimpleNamespace(profile_id="us_etf_daily", enabled_strategies=[], schedule={}),
    )

    with pytest.raises(_StopScheduler):
        live_scheduler.run_scheduler()

    assert "daily_shadow_run" in added_job_ids
    assert "daily_rebalance" in added_job_ids
