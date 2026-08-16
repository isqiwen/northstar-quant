from typer.testing import CliRunner

from northstar_quant import cli


runner = CliRunner()


def test_health_command_reports_blocked_status_by_default(monkeypatch):
    captured: dict[str, object] = {}
    payload = {"status": "blocked", "checks": []}

    monkeypatch.setattr("northstar_quant.cli.run_healthcheck", lambda: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(cli.app, ["health"])

    assert result.exit_code == 0
    assert captured == {"data": payload, "context": {"command": "health"}}


def test_health_command_fails_on_blocked_when_requested(monkeypatch):
    captured: dict[str, object] = {}
    payload = {"status": "blocked", "checks": []}

    monkeypatch.setattr("northstar_quant.cli.run_healthcheck", lambda: payload)
    monkeypatch.setattr(
        "northstar_quant.cli._log_json",
        lambda data, **context: captured.update({"data": data, "context": context}),
    )

    result = runner.invoke(cli.app, ["health", "--fail-on-blocked"])

    assert result.exit_code == 2
    assert captured == {"data": payload, "context": {"command": "health"}}


def test_health_command_allows_degraded_status_when_requested(monkeypatch):
    payload = {"status": "degraded", "checks": []}

    monkeypatch.setattr("northstar_quant.cli.run_healthcheck", lambda: payload)

    result = runner.invoke(cli.app, ["health", "--fail-on-blocked"])

    assert result.exit_code == 0
