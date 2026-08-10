from typer.testing import CliRunner

from northstar_quant.cli import app


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
    providers_result = runner.invoke(app, ["data", "providers"])
    sources_result = runner.invoke(app, ["data", "sources"])

    assert help_result.exit_code == 0
    assert "import-file" in help_result.output
    assert "sources" in help_result.output
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
