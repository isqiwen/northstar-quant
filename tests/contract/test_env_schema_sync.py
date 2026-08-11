"""本地 `.env` 结构迁移器的无密钥契约。"""

from __future__ import annotations

import subprocess
import sys

from tests.support.paths import PROJECT_ROOT


SCRIPT_PATH = PROJECT_ROOT / "scripts" / "dev" / "sync_env_schema.py"
DEV_ENV_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "dev" / "env.sh"


def test_env_schema_sync_preserves_known_values_and_removes_obsolete_keys(tmp_path):
    template = tmp_path / ".env.example"
    active = tmp_path / ".env"
    template.write_text(
        "# 完整示例\nFIRST=example\nSECOND=\n",
        encoding="utf-8",
    )
    active.write_text(
        "FIRST=preserved\nLEGACY_SETTING=obsolete\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--template",
            str(template),
            "--active",
            str(active),
            "--apply",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LEGACY_SETTING" in result.stdout
    assert "preserved" not in result.stdout
    assert active.read_text(encoding="utf-8") == (
        "# 完整示例\nFIRST=preserved\nSECOND=\n"
    )
    backups = list(tmp_path.glob(".env.before-schema-migration-*"))
    assert len(backups) == 1


def test_env_schema_sync_updates_values_from_stdin_without_echoing_them(tmp_path):
    active = tmp_path / ".env"
    active.write_text("FIRST=old\nSECOND=\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--active",
            str(active),
            "--set-stdin",
        ],
        input="FIRST=updated-value\nSECOND=other-value\n",
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "updated-value" not in result.stdout
    assert "other-value" not in result.stdout
    assert active.read_text(encoding="utf-8") == "FIRST=updated-value\nSECOND=other-value\n"


def test_bash_dev_setup_passes_values_by_stdin_and_resets_dev_safety_mode() -> None:
    content = DEV_ENV_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "awk -v value=" not in content
    assert "--set-stdin" in content
    assert 'set_env_value "NORTHSTAR_ENV" "dev"' in content
    assert 'set_env_value "NORTHSTAR_BROKER" "paper"' in content
    assert 'set_env_value "NORTHSTAR_LIVE_TRADING_ENABLED" "false"' in content
    assert 'set_env_value "NORTHSTAR_KILL_SWITCH_ENABLED" "false"' in content


def test_environment_file_temporary_names_are_ignored() -> None:
    sync_script = SCRIPT_PATH.read_text(encoding="utf-8")
    powershell_script = (PROJECT_ROOT / "scripts" / "setup_dev.ps1").read_text(
        encoding="utf-8"
    )
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert 'prefix=f"{path.name}.tmp."' in sync_script
    assert '"{0}.tmp.{1}"' in powershell_script
    assert ".env.*" in gitignore
    assert "..env.*.tmp" in gitignore
