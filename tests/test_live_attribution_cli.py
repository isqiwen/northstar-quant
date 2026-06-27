from typer.testing import CliRunner

from northstar_quant.cli import app


runner = CliRunner()


def test_live_trade_attribution_command_logs_payload(monkeypatch):
    captured: dict[str, object] = {}
    payload = [{"symbol": "SPY", "implementation_shortfall": 50.0}]

    monkeypatch.setattr("northstar_quant.cli.recent_trade_attributions", lambda **_: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(
        app,
        ["live", "trade-attribution", "--profile", "us_etf_daily", "--account", "paper", "--limit", "5"],
    )

    assert result.exit_code == 0
    assert captured["data"] == payload
    assert captured["context"] == {
        "command": "live.trade-attribution",
        "profile": "us_etf_daily",
        "account": "paper",
        "limit": 5,
    }


def test_live_account_attribution_command_logs_payload(monkeypatch):
    captured: dict[str, object] = {}
    payload = [{"equity_change": 260.0, "residual_pnl": 0.0}]

    monkeypatch.setattr("northstar_quant.cli.recent_account_attributions", lambda **_: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(
        app,
        ["live", "account-attribution", "--profile", "us_etf_daily", "--account", "paper", "--limit", "3"],
    )

    assert result.exit_code == 0
    assert captured["data"] == payload
    assert captured["context"] == {
        "command": "live.account-attribution",
        "profile": "us_etf_daily",
        "account": "paper",
        "limit": 3,
    }


def test_live_anomaly_events_command_logs_payload(monkeypatch):
    captured: dict[str, object] = {}
    payload = [{"alert_tag": "执行异常", "summary": "执行损耗达到 60.00"}]

    monkeypatch.setattr("northstar_quant.cli.recent_anomaly_events", lambda **_: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(
        app,
        ["live", "anomaly-events", "--profile", "us_etf_daily", "--account", "paper", "--tag", "执行异常", "--limit", "4"],
    )

    assert result.exit_code == 0
    assert captured["data"] == payload
    assert captured["context"] == {
        "command": "live.anomaly-events",
        "profile": "us_etf_daily",
        "account": "paper",
        "alert_tag": "执行异常",
        "limit": 4,
    }


def test_live_preflight_command_logs_payload(monkeypatch):
    captured: dict[str, object] = {}
    payload = {
        "profile_id": "us_etf_daily",
        "can_trade": False,
        "blocking_failure_count": 1,
        "warning_count": 0,
        "checks": [],
    }

    monkeypatch.setattr("northstar_quant.cli.run_live_preflight", lambda **_: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(
        app,
        ["live", "preflight", "--profile", "us_etf_daily"],
    )

    assert result.exit_code == 0
    assert captured["data"] == payload
    assert captured["context"] == {
        "command": "live.preflight",
        "profile": "us_etf_daily",
    }


def test_live_shadow_run_command_logs_payload(monkeypatch):
    captured: dict[str, object] = {}
    payload = {"run_id": "shadow-run-001", "mode": "shadow_run", "plan_count": 2}

    monkeypatch.setattr("northstar_quant.cli.run_shadow_once", lambda **_: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(
        app,
        ["live", "shadow-run", "--profile", "us_etf_daily"],
    )

    assert result.exit_code == 0
    assert captured["data"] == payload
    assert captured["context"] == {
        "command": "live.shadow-run",
        "profile": "us_etf_daily",
    }


def test_live_run_health_command_logs_payload(monkeypatch):
    captured: dict[str, object] = {}
    payload = [{"run_id": "paper-run-001", "mode": "paper_soak"}]

    monkeypatch.setattr("northstar_quant.cli.recent_run_health", lambda **_: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(
        app,
        ["live", "run-health", "--profile", "us_etf_daily", "--account", "paper", "--mode", "paper_soak", "--limit", "6"],
    )

    assert result.exit_code == 0
    assert captured["data"] == payload
    assert captured["context"] == {
        "command": "live.run-health",
        "profile": "us_etf_daily",
        "account": "paper",
        "mode": "paper_soak",
        "limit": 6,
    }


def test_live_soak_summary_command_logs_payload(monkeypatch):
    captured: dict[str, object] = {}
    payload = {"run_count": 12, "anomaly_trend": "down"}

    monkeypatch.setattr("northstar_quant.cli.soak_summary", lambda **_: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(
        app,
        ["live", "soak-summary", "--profile", "us_etf_daily", "--account", "paper", "--mode", "paper_soak", "--days", "28", "--limit", "10"],
    )

    assert result.exit_code == 0
    assert captured["data"] == payload
    assert captured["context"] == {
        "command": "live.soak-summary",
        "profile": "us_etf_daily",
        "account": "paper",
        "mode": "paper_soak",
        "days": 28,
        "limit": 10,
    }
