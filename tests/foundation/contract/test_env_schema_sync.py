"""本地 `.env` 结构迁移器的无密钥契约。"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.helpers.paths import PROJECT_ROOT


SCRIPT_PATH = PROJECT_ROOT / "scripts" / "dev" / "sync_env_schema.py"
DEV_SETUP_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "dev" / "setup.py"


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


def test_env_schema_sync_second_apply_is_a_true_noop_without_extra_backup(tmp_path):
    template = tmp_path / ".env.example"
    active = tmp_path / ".env"
    template.write_text("FIRST=example\nSECOND=\n", encoding="utf-8")
    active.write_text("FIRST=preserved\n", encoding="utf-8")

    first = subprocess.run(
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
    content_after_first_apply = active.read_text(encoding="utf-8")
    second = subprocess.run(
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

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "结构已完整" in second.stdout
    assert active.read_text(encoding="utf-8") == content_after_first_apply
    assert len(list(tmp_path.glob(".env.before-schema-migration-*"))) == 1


def test_env_schema_cli_refuses_active_symlink_without_touching_target(tmp_path):
    template = tmp_path / ".env.example"
    target = tmp_path / "outside.env"
    active = tmp_path / ".env"
    template.write_text("FIRST=example\n", encoding="utf-8")
    target.write_text("FIRST=preserved\n", encoding="utf-8")
    try:
        active.symlink_to(target)
    except OSError:
        pytest.skip("当前 Windows 权限不允许创建符号链接。")

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

    assert result.returncode == 1
    assert "不能是符号链接" in result.stdout
    assert target.read_text(encoding="utf-8") == "FIRST=preserved\n"


def test_env_schema_cli_refuses_active_symlink_from_stdin_without_touching_target(
    tmp_path,
):
    target = tmp_path / "outside.env"
    active = tmp_path / ".env"
    target.write_text("FIRST=preserved\n", encoding="utf-8")
    try:
        active.symlink_to(target)
    except OSError:
        pytest.skip("当前 Windows 权限不允许创建符号链接。")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--active",
            str(active),
            "--set-stdin",
        ],
        input="FIRST=updated-value\n",
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "不能是符号链接" in result.stdout
    assert "updated-value" not in result.stdout
    assert target.read_text(encoding="utf-8") == "FIRST=preserved\n"


def test_env_schema_cli_refuses_symlinked_active_parent_without_touching_target(tmp_path):
    template = tmp_path / ".env.example"
    outside_directory = tmp_path / "outside"
    active_directory = tmp_path / "linked-directory"
    target = outside_directory / ".env"
    template.write_text("FIRST=example\n", encoding="utf-8")
    outside_directory.mkdir()
    target.write_text("FIRST=preserved\n", encoding="utf-8")
    try:
        active_directory.symlink_to(outside_directory, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 权限不允许创建符号链接。")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--template",
            str(template),
            "--active",
            str(active_directory / ".env"),
            "--apply",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "父目录不能是符号链接" in result.stdout
    assert target.read_text(encoding="utf-8") == "FIRST=preserved\n"


def test_env_schema_cli_still_resolves_template_symlink(tmp_path):
    template_target = tmp_path / ".env.example.target"
    template = tmp_path / ".env.example"
    active = tmp_path / ".env"
    template_target.write_text("FIRST=example\n", encoding="utf-8")
    try:
        template.symlink_to(template_target)
    except OSError:
        pytest.skip("当前 Windows 权限不允许创建符号链接。")

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
    assert active.read_text(encoding="utf-8") == "FIRST=example\n"


def test_cross_platform_dev_setup_passes_values_by_stdin_and_resets_dev_safety_mode() -> None:
    content = DEV_SETUP_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "shell=True" not in content
    assert "--set-stdin" in content
    assert '"NORTHSTAR_ENV": "dev"' in content
    assert '"NORTHSTAR_BROKER": "paper"' in content
    assert '"NORTHSTAR_LIVE_TRADING_ENABLED": "false"' in content
    assert '"NORTHSTAR_KILL_SWITCH_ENABLED": "false"' in content


def test_environment_file_temporary_names_are_ignored() -> None:
    sync_script = SCRIPT_PATH.read_text(encoding="utf-8")
    setup_script = DEV_SETUP_SCRIPT_PATH.read_text(encoding="utf-8")
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert 'prefix=f"{path.name}.tmp."' in sync_script
    assert "secrets.token_hex" in setup_script
    assert ".env.*" in gitignore
    assert "..env.*.tmp" in gitignore
    assert "configs/.app.yaml.tmp.*" in gitignore
