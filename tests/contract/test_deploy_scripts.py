"""Linux 部署脚本的制品与安全门槛测试。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.support.paths import PROJECT_ROOT

ROOT_DIR = PROJECT_ROOT
DEPLOY_DIR = ROOT_DIR / "scripts" / "deploy"


def _resolve_bash_executable() -> str:
    """返回可实际执行的 Bash，避免 Windows 优先命中无发行版的 WSL 占位程序。"""

    if os.name == "nt":
        git_executable = shutil.which("git")
        if git_executable:
            git_bash = Path(git_executable).resolve().parent.parent / "bin" / "bash.exe"
            if git_bash.is_file():
                return str(git_bash)

    bash_executable = shutil.which("bash")
    if bash_executable:
        return bash_executable

    pytest.skip("部署脚本契约测试需要可执行的 Bash；请安装 Git Bash 或配置 WSL 发行版。")


BASH_EXECUTABLE = _resolve_bash_executable()


def _bash_path(path: Path) -> str:
    """将 Windows 路径转换为 Git Bash 可识别的挂载路径。"""

    if os.name != "nt":
        return str(path)

    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1:
        raise ValueError(f"无法转换为 Git Bash 路径：{resolved}")
    return f"/{drive}{resolved.as_posix()[2:]}"


def _run_bash(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    """以 UTF-8 捕获部署脚本输出，兼容 Linux Bash 与 Windows Git Bash。"""

    return subprocess.run(
        [BASH_EXECUTABLE, *args],
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


def test_top_level_deploy_entrypoint_forwards_help() -> None:
    result = _run_bash(
        _bash_path(ROOT_DIR / "scripts" / "deploy.sh"),
        "--help",
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "首次部署" in result.stdout
    assert "scripts/deploy.sh" in result.stdout


def _run_safety_check(
    env_file: Path,
    *,
    service_mode: str,
    confirmation: str,
) -> subprocess.CompletedProcess[str]:
    command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/safety.sh"
deploy_validate_production_env "$2" "$3" "$4"
"""
    return _run_bash(
        *[
            "-c",
            command,
            BASH_EXECUTABLE,
            _bash_path(DEPLOY_DIR),
            _bash_path(env_file),
            service_mode,
            confirmation,
        ],
        check=False,
        capture_output=True,
    )


def test_build_artifact_contains_only_runtime_sources(tmp_path: Path) -> None:
    artifact_path = tmp_path / "northstar-quant-test.tar.gz"
    env = {
        **os.environ,
        "ARTIFACT_DIR": _bash_path(tmp_path),
        "ARTIFACT_NAME": artifact_path.name,
        "ARTIFACT_PATH": _bash_path(artifact_path),
        "REVISION": "test",
        "STAMP": "20260730000000",
    }

    _run_bash(
        _bash_path(DEPLOY_DIR / "build-artifact.sh"),
        cwd=ROOT_DIR,
        env=env,
        check=True,
        capture_output=True,
    )

    with tarfile.open(artifact_path, "r:gz") as archive:
        names = {name.removeprefix("./") for name in archive.getnames()}

    assert "pyproject.toml" in names
    assert "uv.lock" in names
    assert "src/northstar_quant/cli.py" in names
    assert "configs/profiles/offline/cn_futures_daily_trend_offline.yaml" in names
    assert ".env" not in names
    assert ".venv" not in names
    assert not any(Path(name).name.startswith("._") for name in names)
    assert not any(name.startswith("logs/") for name in names)
    assert not any(name.startswith("storage/") for name in names)
    assert not any(name.startswith("reports/") for name in names)
    assert not any(name.startswith("tests/") for name in names)


def test_health_deploy_accepts_safe_production_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",
                "NORTHSTAR_BROKER=paper",
                "NORTHSTAR_LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_safety_check(
        env_file,
        service_mode="health",
        confirmation="NO",
    )

    assert result.returncode == 0, result.stderr


def test_health_deploy_rejects_live_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",
                "NORTHSTAR_BROKER=ctp",
                "NORTHSTAR_LIVE_TRADING_ENABLED=true",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_safety_check(
        env_file,
        service_mode="health",
        confirmation="YES",
    )

    assert result.returncode != 0
    assert "health 模式要求" in result.stderr


def test_non_paper_scheduler_requires_explicit_confirmation(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",
                "NORTHSTAR_BROKER=ctp",
                "NORTHSTAR_LIVE_TRADING_ENABLED=true",
            ]
        ),
        encoding="utf-8",
    )

    rejected = _run_safety_check(
        env_file,
        service_mode="scheduler",
        confirmation="NO",
    )
    accepted = _run_safety_check(
        env_file,
        service_mode="scheduler",
        confirmation="YES",
    )

    assert rejected.returncode != 0
    assert "CONFIRM_LIVE_DEPLOY=YES" in rejected.stderr
    assert accepted.returncode == 0, accepted.stderr
