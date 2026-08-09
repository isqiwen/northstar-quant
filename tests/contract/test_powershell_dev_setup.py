"""Windows PowerShell 开发入口的安全契约。"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.support.paths import PROJECT_ROOT

SCRIPT_PATH = PROJECT_ROOT / "scripts" / "setup_dev.ps1"


def test_powershell_dev_setup_uses_local_postgresql_and_safe_runtime_mode() -> None:
    assert SCRIPT_PATH.read_bytes().startswith(b"\xef\xbb\xbf")

    content = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "docker compose up -d postgres" in content
    assert 'Ensure-Database "northstar_test"' in content
    assert "NORTHSTAR_TEST_DATABASE_URL" in content
    assert '$env:NORTHSTAR_BROKER = "paper"' in content
    assert '$env:NORTHSTAR_LIVE_TRADING_ENABLED = "false"' in content
    assert "uv sync --extra dev --locked" in content
    assert "uv run pytest" in content
    assert "uv run ruff check ." in content


def test_powershell_dev_setup_does_not_write_env_or_start_trading_paths() -> None:
    content = SCRIPT_PATH.read_text(encoding="utf-8").lower()

    assert "data download" not in content
    assert "live run" not in content
    assert "live scheduler" not in content
    assert "set-content" not in content
    assert "add-content" not in content
    assert "writealltext" not in content


def test_powershell_dev_setup_has_valid_syntax_when_powershell_is_available() -> None:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("当前平台未安装 PowerShell；由 Windows 开发机执行语法校验。")

    escaped_script_path = str(SCRIPT_PATH).replace("'", "''")
    parser_command = (
        "& { param([string]$Path) "
        "$parseErrors = @(); "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$Path, [ref]$null, [ref]$parseErrors) | Out-Null; "
        "if ($parseErrors.Count -gt 0) { "
        "$parseErrors | ForEach-Object { Write-Error $_ }; exit 1 "
        "}"
        f" }} '{escaped_script_path}'"
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-Command", parser_command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
