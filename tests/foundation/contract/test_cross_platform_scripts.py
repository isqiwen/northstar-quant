"""Windows/Linux 共用控制面及 Linux 目标端边界的契约。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.paths import PROJECT_ROOT


PYTHON_ENTRYPOINTS = (
    "scripts/dev/check_env.py",
    "scripts/dev/setup.py",
    "scripts/deploy/inventory.py",
    "scripts/deploy/preflight.py",
    "scripts/deploy/package.py",
    "scripts/deploy/deploy.py",
    "scripts/ops/health.py",
    "scripts/ops/logs.py",
    "scripts/ops/backup.py",
    "scripts/ops/diagnose.py",
    "scripts/maintenance/backup_bundle.py",
    "scripts/maintenance/restore_drill.py",
)


def _run_python(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cross_platform_python_entrypoints_have_portable_help() -> None:
    for relative_path in PYTHON_ENTRYPOINTS:
        result = _run_python(PROJECT_ROOT / relative_path, "--help")
        assert result.returncode == 0, f"{relative_path}: {result.stdout}\n{result.stderr}"


def test_workstation_check_is_read_only_and_reports_optional_tools() -> None:
    result = _run_python(PROJECT_ROOT / "scripts/dev/check_env.py", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    checks = json.loads(result.stdout)
    assert {item["name"] for item in checks} >= {
        "Python",
        "uv",
        "just",
        "Git",
        "Docker",
        "Docker Compose v2",
        "Docker daemon",
        "SSH",
        "OpenSSH ssh-keygen",
    }


def test_tool_bootstrap_default_is_a_non_installing_preview() -> None:
    """默认路径可以在 CI/工作站执行，但不得调用系统安装器。"""

    result = _run_python(PROJECT_ROOT / "scripts/dev/setup.py", "--bootstrap-tools")

    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "开始安装：" not in output
    assert "未执行任何系统安装" in output or "默认只展示" in output


def test_tool_bootstrap_keeps_explicit_confirmation_and_no_shell_contract() -> None:
    setup = (PROJECT_ROOT / "scripts/dev/setup.py").read_text(encoding="utf-8")
    planner = (PROJECT_ROOT / "scripts/dev/tool_bootstrap.py").read_text(encoding="utf-8")

    for option in (
        '"--bootstrap-tools"',
        '"--install-docker"',
        '"--apply"',
        '"--confirm-tool-install"',
        '"--confirm-docker-install"',
        'args.confirm_tool_install != "YES"',
        'args.confirm_docker_install != "YES"',
    ):
        assert option in setup
    assert setup.index("if not args.apply") < setup.index("execute_install_plan(steps)")
    assert "shell=True" not in setup
    assert "shell=True" not in planner
    assert '("usermod"' not in planner
    assert '("systemctl"' not in planner
    assert "curl |" not in planner


def test_ops_dry_run_never_requires_ssh_or_linux_target(tmp_path: Path) -> None:
    inventory = tmp_path / "deploy.env"
    inventory.write_text("DEPLOY_HOST=ops@example.invalid\n", encoding="utf-8")

    for name in ("health.py", "logs.py", "backup.py", "diagnose.py"):
        result = _run_python(
            PROJECT_ROOT / "scripts" / "ops" / name,
            "--inventory",
            str(inventory),
            "--dry-run",
        )
        assert result.returncode == 0, f"{name}: {result.stdout}\n{result.stderr}"
        assert "未连接 Linux 服务器" in result.stdout


def test_justfile_is_thin_cross_platform_command_router() -> None:
    justfile = (PROJECT_ROOT / "justfile").read_text(encoding="utf-8")

    for recipe in (
        "dev-check:",
        "dev-bootstrap:",
        "dev-bootstrap-docker:",
        "setup:",
        "setup-postgres:",
        "dev-setup:",
        "dev-postgres:",
        "db-up:",
        "db-migrate:",
        "test-unit:",
        "test-backtest:",
        "test-cli:",
        "test:",
        "lint:",
        "typecheck:",
        "candidate-acceptance:",
        "deploy-prod signing_key inventory='deploy.env':",
        "ops-health inventory='deploy.env':",
        "ops-backup inventory='deploy.env':",
    ):
        assert recipe in justfile
    assert "systemctl" not in justfile
    assert "docker compose" not in justfile


def test_vscode_workspace_uses_cross_platform_and_explicitly_confirmed_tasks() -> None:
    """工作区任务不能绕过 just、安全确认或 Windows 路径兼容性。"""

    workspace = PROJECT_ROOT / ".vscode"
    tasks = json.loads((workspace / "tasks.json").read_text(encoding="utf-8"))
    settings = json.loads((workspace / "settings.json").read_text(encoding="utf-8"))
    tasks_by_label = {task["label"]: task for task in tasks["tasks"]}

    assert tasks["options"] == {"cwd": "${workspaceFolder}"}
    assert "python.defaultInterpreterPath" not in settings
    assert settings["python.testing.pytestArgs"] == [
        "tests/data/unit",
        "tests/intelligence/unit",
        "tests/research/unit",
        "tests/portfolio_risk/unit",
        "tests/trading_execution/unit",
        "tests/foundation/unit",
    ]

    for label, recipe in (
        ("开发：环境检查", "dev-check"),
        ("开发：初始化安全配置与依赖", "setup"),
        ("数据库：初始化 PostgreSQL 并迁移（显式）", "setup-postgres"),
        ("测试：领域单元测试", "test-unit"),
        ("测试：回测研究测试", "test-backtest"),
        ("测试：CLI 契约测试", "test-cli"),
        ("质量：Ruff 与 mypy 基线", "check"),
    ):
        task = tasks_by_label[label]
        assert task["type"] == "process"
        assert task["command"] == "just"
        assert task["args"] == [recipe]

    tool_install = tasks_by_label["开发：安装基础工具（需确认）"]
    assert tool_install["args"][-1] == "${input:confirmToolInstall}"
    assert "YES" not in tool_install["args"]

    docker_install = tasks_by_label["开发：安装 Docker（需双重确认）"]
    assert docker_install["args"][-3:] == [
        "${input:confirmToolInstall}",
        "--confirm-docker-install",
        "${input:confirmDockerInstall}",
    ]
    assert "YES" not in docker_install["args"]

    deploy_preview = tasks_by_label["部署：预览 Linux 发布（不连接服务器）"]
    assert deploy_preview["type"] == "process"
    assert deploy_preview["command"] == "uv"
    assert "--apply" not in deploy_preview["args"]
    assert "--dry-run" in deploy_preview["args"]
    assert "scripts/setup_dev.sh" not in (workspace / "tasks.json").read_text(encoding="utf-8")


def test_justfile_is_parseable_when_just_is_available() -> None:
    executable = shutil.which("just")
    if executable is None:
        pytest.skip("当前工作站未安装 just；bootstrap 契约不触发安装。")

    result = subprocess.run(
        [executable, "--list"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "dev-bootstrap" in result.stdout
    assert "setup" in result.stdout


def test_tier_one_ci_installs_and_exercises_just() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert workflow.count("uses: extractions/setup-just@v3") == 2
    assert workflow.count("just --list") == 2
    assert "runs-on: ubuntu-24.04" in workflow
    assert "image: postgres:16" in workflow
    assert "postgresql-client-16" in workflow
    for command in (
        "just dev-check",
        "just test-unit",
        "just test-backtest",
        "just test-cli",
        "just candidate-acceptance",
        "just check",
        "command -v pg_dump",
        "command -v pg_restore",
        "command -v psql",
        "SHOW server_version_num",
    ):
        assert command in workflow


def test_deploy_control_plane_does_not_require_local_bash_or_git_bash() -> None:
    deploy = (PROJECT_ROOT / "scripts" / "deploy" / "deploy.py").read_text(encoding="utf-8")

    assert "bash.exe" not in deploy
    assert "find_bash_executable" not in deploy
    assert 'shutil.which("ssh")' in deploy
    assert 'shutil.which("scp")' not in deploy
    assert "sudo -n env" not in deploy
    assert "StrictHostKeyChecking=yes" in deploy
    assert "--skip-tests" not in deploy
    assert "--skip-ruff" not in deploy
    assert "--allow-dirty" not in deploy
    assert "--upload-ntfy-bootstrap" not in deploy
    assert "--confirm-ntfy-bootstrap" not in deploy
    assert "--signing-key" in deploy
    assert 'f"sudo -n {ROOT_RUNNER_PATH} identity"' in deploy
    assert 'f"sudo -n {ROOT_RUNNER_PATH} submit"' in deploy
    assert "subprocess.Popen(command, stdin=subprocess.PIPE)" in deploy
    assert "write_submission(process.stdin, submission)" in deploy
    assert "build_control_artifact(" in deploy
    assert "build_manifest(" in deploy
    assert "sign_manifest(" in deploy
    assert "sign_environment(" in deploy
    for forbidden_remote_staging_reference in ("remote_paths", "work_dir", "/tmp"):
        assert forbidden_remote_staging_reference not in deploy

    remote_ops = (PROJECT_ROOT / "scripts" / "ops" / "_remote.py").read_text(encoding="utf-8")
    assert "StrictHostKeyChecking=yes" in remote_ops
    assert "timeout=_REMOTE_OPERATION_TIMEOUT_SECONDS" in remote_ops
    assert '"env",' in remote_ops
    assert '"-i",' in remote_ops
    assert '"/bin/bash",' in remote_ops
    assert '"-p",' in remote_ops


def test_linux_remote_ops_are_read_only_or_fail_closed() -> None:
    remote_dir = PROJECT_ROOT / "scripts" / "ops" / "remote" / "linux"
    for name in ("health.sh", "logs.sh", "diagnose.sh", "backup.sh", "restore.sh"):
        assert (remote_dir / name).is_file()

    backup = (remote_dir / "backup.sh").read_text(encoding="utf-8")
    diagnose = (remote_dir / "diagnose.sh").read_text(encoding="utf-8")
    restore = (remote_dir / "restore.sh").read_text(encoding="utf-8")
    health = (remote_dir / "health.sh").read_text(encoding="utf-8")
    logs = (remote_dir / "logs.sh").read_text(encoding="utf-8")

    for script in (backup, diagnose, restore, health, logs):
        assert script.startswith("#!/bin/bash -p\n")
        assert "unset BASH_ENV ENV CDPATH" in script
        assert 'PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' in script

    assert "ops backup status" in backup
    assert "runuser -u" in backup
    assert 'readonly CANONICAL_SERVICE_USER="northstar"' in backup
    assert 'readonly CANONICAL_APP_ROOT="/opt/northstar"' in backup
    assert 'readonly CANONICAL_SERVICE_NAME="northstar-quant"' in diagnose
    assert 'readonly CANONICAL_APP_ROOT="/opt/northstar"' in diagnose
    assert 'if [ "${service_name}" != "northstar-quant" ]; then' in health
    assert 'if [ "${service_name}" != "northstar-quant" ]; then' in logs
    assert "/srv/" not in backup
    assert "/srv/" not in diagnose
    assert "被拒绝" in restore
