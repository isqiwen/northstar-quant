import subprocess
import sys
from types import SimpleNamespace

from typer.testing import CliRunner

import northstar_quant.cli as cli

app = cli.app


runner = CliRunner()


def test_root_help_supports_short_flag_and_chinese_descriptions():
    result = runner.invoke(app, ["-h"])

    assert result.exit_code == 0
    assert "--install-completion" in result.output
    assert "为当前 shell 安装自动补全" in result.output
    assert "--show-completion" in result.output
    assert "输出当前 shell" in result.output
    assert "自动补全脚本" in result.output
    assert "复制或按需定制" in result.output
    assert "--help" in result.output
    assert "显示帮助并退出" in result.output
    assert "init-db" in result.output
    assert "初始化本地数据库表结构" in result.output
    assert "sample-data" not in result.output
    assert "health" in result.output
    assert "检查项目当前运行状态" in result.output


def test_root_help_short_and_long_flags_match():
    short_help = runner.invoke(app, ["-h"])
    long_help = runner.invoke(app, ["--help"])

    assert short_help.exit_code == 0
    assert long_help.exit_code == 0
    assert short_help.output == long_help.output


def test_data_help_exposes_local_import_and_actual_daily_provider():
    help_result = runner.invoke(app, ["data", "--help"])
    cleanup_help_result = runner.invoke(app, ["data", "cleanup", "--help"])
    providers_result = runner.invoke(app, ["data", "providers"])
    sources_result = runner.invoke(app, ["data", "sources"])

    assert help_result.exit_code == 0
    assert "import-file" in help_result.output
    assert "sources" in help_result.output
    assert "cleanup" in help_result.output
    assert cleanup_help_result.exit_code == 0
    assert "--apply" in cleanup_help_result.output
    assert "--config" in cleanup_help_result.output
    assert providers_result.exit_code == 0
    assert "akshare_actual_daily" in providers_result.output
    assert "Traceback" not in providers_result.output
    assert sources_result.exit_code == 0
    assert "wind_wds_server_v1" in sources_result.output
    assert "procurement_pending" in sources_result.output


def test_backtest_help_exposes_single_profile_driven_entrypoint():
    result = runner.invoke(app, ["backtest", "--help"])

    assert result.exit_code == 0
    assert "run" in result.output
    assert "event" not in result.output
    assert "bt" not in result.output


def test_research_help_exposes_configuration_backed_admission_assessment():
    result = runner.invoke(app, ["research", "--help"])

    assert result.exit_code == 0
    assert "assess" in result.output


def test_live_help_exposes_separate_signal_execution_and_risk_commands():
    result = runner.invoke(app, ["live", "--help"])

    assert result.exit_code == 0
    assert "signal" in result.output
    assert "execute" in result.output
    assert "risk-check" in result.output


def test_ops_backup_status_help_is_explicitly_read_only():
    ops_help = runner.invoke(app, ["ops", "--help"])
    backup_help = runner.invoke(app, ["ops", "backup", "status", "--help"])

    assert ops_help.exit_code == 0
    assert "backup" in ops_help.output
    assert backup_help.exit_code == 0
    assert "--config" in backup_help.output
    assert "不执行备份或恢复" in backup_help.output


def test_dashboard_run_keeps_streamlit_private_and_hardened(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(dashboard_host="0.0.0.0", dashboard_port=8517),
    )
    monkeypatch.setattr(subprocess, "call", lambda command: commands.append(command) or 0)

    result = runner.invoke(app, ["dashboard", "run"])

    assert result.exit_code == 0, result.output
    assert commands == [
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "src/northstar_quant/monitoring/dashboard.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            "8517",
            "--server.headless",
            "true",
            "--server.enableCORS",
            "true",
            "--server.enableXsrfProtection",
            "true",
            "--server.enableStaticServing",
            "false",
            "--server.fileWatcherType",
            "none",
            "--browser.gatherUsageStats",
            "false",
            "--client.toolbarMode",
            "viewer",
            "--client.showErrorDetails",
            "none",
            "--client.showErrorLinks",
            "false",
        ]
    ]
