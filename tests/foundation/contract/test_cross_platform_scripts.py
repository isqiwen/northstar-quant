"""Windows/Linux 共用控制面及 Linux 目标端边界的契约。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.paths import PROJECT_ROOT


PYTHON_ENTRYPOINTS = (
    "scripts/dev/bootstrap_just.py",
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
DOMAIN_UNIT_TEST_PATHS = (
    "tests/application/unit",
    "tests/data/unit",
    "tests/intelligence/unit",
    "tests/research/unit",
    "tests/portfolio_risk/unit",
    "tests/trading_execution/unit",
    "tests/foundation/unit",
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
        "pg_isready",
        "psql",
        "createdb",
        "pg_dump",
        "pg_restore",
        "本机 PostgreSQL",
        "SSH",
        "OpenSSH ssh-keygen",
    }


def test_tool_bootstrap_default_is_a_non_installing_preview() -> None:
    """默认路径可在本地质量门禁或工作站执行，但不得调用系统安装器。"""

    result = _run_python(PROJECT_ROOT / "scripts/dev/setup.py", "--bootstrap-tools")

    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "开始安装：" not in output
    assert "未执行任何安装命令" in output or "默认只展示" in output


def test_tool_bootstrap_keeps_explicit_confirmation_and_no_shell_contract() -> None:
    setup = (PROJECT_ROOT / "scripts/dev/setup.py").read_text(encoding="utf-8")
    planner = (PROJECT_ROOT / "scripts/dev/tool_bootstrap.py").read_text(encoding="utf-8")

    for option in (
        '"--bootstrap-tools"',
        '"--apply"',
        '"--confirm-tool-install"',
        'tool_confirmation != "YES"',
    ):
        assert option in setup
    assert setup.index("if not args.apply") < setup.index("execute_install_plan(steps)")
    assert "shell=True" not in setup
    assert "shell=True" not in planner
    assert "docker" not in setup.casefold()
    assert "docker" not in planner.casefold()
    assert "build_native_postgresql_plan" in setup
    assert "build_native_postgresql_plan" in planner
    assert '("sudo", "systemctl", "enable", "--now", "postgresql")' in planner
    assert "--install-postgres" not in setup
    assert setup.index("_ensure_native_postgresql_for_workstation") < setup.index(
        "readiness = check_environment"
    )
    assert "curl |" not in planner
    assert '"--target"' in planner
    assert 'python_executable, "-m", "pipx", "install", "--force", "uv"' in planner
    assert '"PIPX_BIN_DIR": str(project_tool_root / "bin")' in planner
    assert "JUST_BOOTSTRAP_SCRIPT" in planner
    assert "ensurepath" not in planner
    assert "--break-system-packages" not in planner


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
        "setup:",
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
        "deploy-preview inventory='deploy.env':",
        "deploy-prod signing_key inventory='deploy.env':",
        "ops-health inventory='deploy.env':",
        "ops-backup inventory='deploy.env':",
    ):
        assert recipe in justfile
    assert "just_executable()" in justfile
    assert "systemctl" not in justfile
    assert "docker" not in justfile.casefold()


def test_vscode_workspace_exposes_only_cross_platform_daily_tasks() -> None:
    """工作区只展示日常入口；首次工具 bootstrap 是受控的 Python 例外。"""

    workspace = PROJECT_ROOT / ".vscode"
    tasks = json.loads((workspace / "tasks.json").read_text(encoding="utf-8"))
    settings = json.loads((workspace / "settings.json").read_text(encoding="utf-8"))
    daily_tasks = (
        ("开发：初始化", "python", ["scripts/dev/setup.py", "--initialize-workstation"]),
        ("测试：全部", "python", ["scripts/dev/run_just.py", "test"]),
        ("质量：检查", "python", ["scripts/dev/run_just.py", "check"]),
        ("开发：环境诊断", "python", ["scripts/dev/run_just.py", "dev-check"]),
    )
    assert [task["label"] for task in tasks["tasks"]] == [task[0] for task in daily_tasks]
    tasks_by_label = {task["label"]: task for task in tasks["tasks"]}

    assert tasks["options"] == {"cwd": "${workspaceFolder}"}
    assert "inputs" not in tasks
    assert "python.defaultInterpreterPath" not in settings
    assert settings["python.testing.pytestArgs"] == list(DOMAIN_UNIT_TEST_PATHS)

    for label, command, arguments in daily_tasks:
        task = tasks_by_label[label]
        assert task["type"] == "process"
        assert task["command"] == command
        assert task["args"] == arguments

    assert tasks_by_label["测试：全部"]["group"] == {"kind": "test", "isDefault": True}
    assert tasks_by_label["质量：检查"]["group"] == {"kind": "build", "isDefault": True}

    justfile = (PROJECT_ROOT / "justfile").read_text(encoding="utf-8")
    assert (
        "dev-check:\n"
        "    python scripts/dev/check_env.py --require-config --require-postgres --require-just --require-git"
        in justfile
    )
    assert "setup:\n    python scripts/dev/setup.py --initialize-workstation" in justfile
    expected_unit_recipe = (
        "test-unit:\n"
        "    python scripts/dev/run_uv.py run --offline --no-sync pytest "
        + " ".join(DOMAIN_UNIT_TEST_PATHS)
        + " -q"
    )
    assert expected_unit_recipe in justfile
    setup = (PROJECT_ROOT / "scripts/dev/setup.py").read_text(encoding="utf-8")
    expected_setup_unit_paths = "\n".join(
        f'                "{path}",' for path in DOMAIN_UNIT_TEST_PATHS
    )
    assert expected_setup_unit_paths in setup
    assert "setup-postgres:" not in justfile
    assert (
        "dev-postgres:\n"
        "    python scripts/dev/check_env.py --require-postgres\n"
        "    python scripts/dev/run_just.py env-bootstrap"
        in justfile
    )
    assert (
        "db-up:\n"
        "    python scripts/dev/check_env.py --require-postgres\n"
        "    python scripts/dev/run_just.py env-bootstrap"
        in justfile
    )
    deploy_preview = justfile.split("deploy-preview inventory='deploy.env':", maxsplit=1)[1].split(
        "\n\n# 默认部署命令", maxsplit=1
    )[0]
    assert "--dry-run" in deploy_preview
    assert "--apply" not in deploy_preview
    assert "scripts/setup_dev.sh" not in (workspace / "tasks.json").read_text(encoding="utf-8")
    assert settings["files.exclude"]["**/.northstar"] is True
    assert settings["files.exclude"]["**/.venv"] is True
    assert settings["files.exclude"]["**/.venv.bootstrap-*"] is True
    assert settings["files.exclude"]["**/.venv.previous-*"] is True
    assert settings["files.exclude"]["**/..venv.bootstrap-*"] is True
    assert settings["files.watcherExclude"]["**/.venv.bootstrap-*/**"] is True
    assert settings["files.watcherExclude"]["**/.northstar/**"] is True


def test_application_unit_tests_receive_the_unit_marker() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "unit",
            "tests/application/unit/test_agent_tools.py",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_agent_tools.py" in result.stdout


def test_justfile_is_parseable_when_repository_local_just_is_available() -> None:
    executable_name = "just.exe" if sys.platform == "win32" else "just"
    if not (PROJECT_ROOT / ".northstar" / "bin" / executable_name).is_file():
        pytest.skip("当前工作站尚未安装仓库本地 just；bootstrap 契约不触发安装。")

    result = _run_python(PROJECT_ROOT / "scripts/dev/run_just.py", "--list")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "dev-bootstrap" in result.stdout
    assert "setup" in result.stdout


def test_repository_does_not_declare_a_github_actions_workflow() -> None:
    """本地质量门禁不依赖或维护 GitHub Actions workflow。"""

    workflows = PROJECT_ROOT / ".github" / "workflows"
    assert not tuple(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in workflows.glob(pattern)
        if path.is_file()
    )


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
