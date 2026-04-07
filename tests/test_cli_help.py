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
    assert "sample-data" in result.output
    assert "生成项目自带的样例行情数据" in result.output
    assert "health" in result.output
    assert "检查项目当前运行状态" in result.output


def test_root_help_short_and_long_flags_match():
    short_help = runner.invoke(app, ["-h"])
    long_help = runner.invoke(app, ["--help"])

    assert short_help.exit_code == 0
    assert long_help.exit_code == 0
    assert short_help.output == long_help.output
