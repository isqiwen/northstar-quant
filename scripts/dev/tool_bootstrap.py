"""跨平台开发工具与原生 PostgreSQL 安装计划。

常规开发工具计划默认只生成可审阅的命令，不执行安装。调用方必须在收到明确的
``YES`` 确认后才可以执行计划。原生 PostgreSQL 的系统安装计划仅供高层
``--initialize-workstation`` 调用；它同样不使用 shell。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys


OS_RELEASE_PATH = Path("/etc/os-release")
JUST_BOOTSTRAP_SCRIPT = Path(__file__).with_name("bootstrap_just.py")
_SAFE_OS_RELEASE_VALUE = re.compile(r"^[a-z0-9._-]+$")
NATIVE_POSTGRESQL_PACKAGES = ("postgresql", "postgresql-client")


class BootstrapPlanError(RuntimeError):
    """表示无法安全生成当前主机的安装计划。"""


@dataclass(frozen=True)
class InstallStep:
    """一个不经 shell 展开的、可审阅的工具安装步骤。"""

    label: str
    command: tuple[str, ...]
    input_text: str | None = None
    note: str | None = None
    environment: Mapping[str, str] | None = None


def _read_os_release(path: Path = OS_RELEASE_PATH) -> dict[str, str]:
    """读取受支持 Linux 的最小发行版标识，拒绝缺失或异常数据。"""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise BootstrapPlanError(f"无法读取 {path}，无法安全生成 Linux 安装计划。") from error

    values: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')

    distribution = values.get("ID", "").lower()
    codename = values.get("VERSION_CODENAME", "").lower()
    if distribution not in {"ubuntu", "debian"}:
        raise BootstrapPlanError(
            "Linux 工具 bootstrap 仅正式支持 Ubuntu/Debian；当前发行版不会执行安装。"
        )
    if not codename or not _SAFE_OS_RELEASE_VALUE.fullmatch(codename):
        raise BootstrapPlanError("无法识别安全的 Linux VERSION_CODENAME，已拒绝生成安装计划。")
    return {"id": distribution, "codename": codename}


def format_command(command: Iterable[str]) -> str:
    """用当前平台可读的形式展示命令，仅用于日志。"""

    return shlex.join(list(command))


def _repository_local_uv_steps(
    *,
    project_tool_root: Path,
    python_executable: str,
) -> list[InstallStep]:
    """Install pipx and uv under the untracked repository tool root."""

    pipx_modules = project_tool_root / "bootstrap" / "pipx"
    environment = {
        "PIP_CACHE_DIR": str(project_tool_root / "cache" / "pip"),
        "PIPX_HOME": str(project_tool_root / "pipx"),
        "PIPX_BIN_DIR": str(project_tool_root / "bin"),
        "PIPX_DEFAULT_PYTHON": python_executable,
        "PIPX_MAN_DIR": str(project_tool_root / "man"),
        "PYTHONPATH": str(pipx_modules),
        "XDG_CACHE_HOME": str(project_tool_root / "cache"),
        "XDG_STATE_HOME": str(project_tool_root / "state"),
    }
    return [
        InstallStep(
            "通过当前 Python 在仓库 .northstar 中安装 pipx",
            (
                python_executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--target",
                str(pipx_modules),
                "--upgrade",
                "pipx",
            ),
            note="使用 --target，不会修改受管理的系统 Python site-packages。",
            environment=environment,
        ),
        InstallStep(
            "通过仓库本地 pipx 安装 uv",
            (python_executable, "-m", "pipx", "install", "--force", "uv"),
            note="uv、pipx venv、缓存和状态均位于 .northstar；项目命令按路径调用 uv。",
            environment=environment,
        ),
    ]


def _repository_local_just_steps(
    *,
    project_tool_root: Path,
    python_executable: str,
) -> list[InstallStep]:
    """Install the pinned official just binary under the repository tool root."""

    return [
        InstallStep(
            "下载并校验仓库本地 just",
            (
                python_executable,
                str(JUST_BOOTSTRAP_SCRIPT),
                "--tool-root",
                str(project_tool_root),
            ),
            note=(
                "下载固定版本的官方 Linux/Windows x86_64 发布包；"
                "SHA-256 校验后仅写入 .northstar/bin。"
            ),
        )
    ]


def _windows_plan(
    *,
    missing_tools: set[str],
    project_tool_root: Path,
    python_executable: str,
) -> list[InstallStep]:
    package_ids = {"git": "Git.Git"}

    selected_ids: list[str] = []
    for tool, package_id in package_ids.items():
        if tool in missing_tools and package_id not in selected_ids:
            selected_ids.append(package_id)
    steps = [
        InstallStep(
            label=f"通过 winget 安装 {package_id}",
            command=("winget", "install", "--id", package_id, "--exact", "--source", "winget"),
        )
        for package_id in selected_ids
    ]
    if "just" in missing_tools:
        steps.extend(
            _repository_local_just_steps(
                project_tool_root=project_tool_root,
                python_executable=python_executable,
            )
        )
    if "uv" in missing_tools:
        steps.extend(
            _repository_local_uv_steps(
                project_tool_root=project_tool_root,
                python_executable=python_executable,
            )
        )
    return steps


def _linux_plan(
    *,
    missing_tools: set[str],
    project_tool_root: Path,
    python_executable: str,
    os_release_path: Path,
) -> list[InstallStep]:
    _read_os_release(os_release_path)
    steps: list[InstallStep] = []

    packages = [tool for tool in ("git",) if tool in missing_tools]
    if packages:
        steps.extend(
            (
                InstallStep("更新 APT 软件包索引", ("sudo", "apt-get", "update")),
                InstallStep(
                    "安装所需的基础开发工具",
                    ("sudo", "apt-get", "install", "--yes", *packages),
                ),
            )
        )

    if "just" in missing_tools:
        steps.extend(
            _repository_local_just_steps(
                project_tool_root=project_tool_root,
                python_executable=python_executable,
            )
        )

    if "uv" in missing_tools:
        steps.extend(
            _repository_local_uv_steps(
                project_tool_root=project_tool_root,
                python_executable=python_executable,
            )
        )

    return steps


def build_install_plan(
    *,
    missing_tools: Iterable[str],
    system_name: str | None = None,
    os_release_path: Path = OS_RELEASE_PATH,
    project_tool_root: Path | None = None,
    python_executable: str | None = None,
) -> list[InstallStep]:
    """为缺失工具生成安装计划，绝不执行命令。"""

    missing = set(missing_tools)
    current_system = system_name or platform.system()
    tool_root = project_tool_root or Path(".northstar")
    python = python_executable or sys.executable
    if current_system == "Windows":
        return _windows_plan(
            missing_tools=missing,
            project_tool_root=tool_root,
            python_executable=python,
        )
    if current_system == "Linux":
        return _linux_plan(
            missing_tools=missing,
            project_tool_root=tool_root,
            python_executable=python,
            os_release_path=os_release_path,
        )
    raise BootstrapPlanError(
        "开发工具 bootstrap 仅正式支持 Windows 与 Ubuntu/Debian Linux；当前平台只提供检查。"
    )


def build_native_postgresql_plan(
    *,
    install_packages: bool,
    system_name: str | None = None,
    os_release_path: Path = OS_RELEASE_PATH,
) -> list[InstallStep]:
    """生成 Ubuntu/Debian 本机 PostgreSQL 的受限系统安装/启动计划。

    该计划只适用于高层工作站初始化：它安装发行版维护的 server/client 包，并且只对
    ``postgresql`` systemd unit 执行 ``enable --now``。不会修改 PostgreSQL 配置、
    数据目录、角色、数据库或 schema；调用方仍负责在执行前验证目标与权限。
    """

    current_system = system_name or platform.system()
    if current_system != "Linux":
        raise BootstrapPlanError(
            "原生 PostgreSQL 默认安装仅支持 Ubuntu/Debian Linux；当前平台只提供检查。"
        )
    _read_os_release(os_release_path)

    steps: list[InstallStep] = []
    if install_packages:
        steps.extend(
            (
                InstallStep("更新 APT 软件包索引", ("sudo", "apt-get", "update")),
                InstallStep(
                    "安装本机 PostgreSQL 服务端与客户端",
                    (
                        "sudo",
                        "apt-get",
                        "install",
                        "--yes",
                        "--no-install-recommends",
                        *NATIVE_POSTGRESQL_PACKAGES,
                    ),
                    note="使用发行版维护的软件包；不会写入仓库或修改 PostgreSQL 数据。",
                ),
            )
        )
    steps.append(
        InstallStep(
            "启用并启动本机 PostgreSQL 服务",
            ("sudo", "systemctl", "enable", "--now", "postgresql"),
            note="仅管理 Ubuntu/Debian 的默认 postgresql systemd unit；不会停止、重置或删除服务数据。",
        )
    )
    return steps


def execute_install_plan(
    steps: Iterable[InstallStep],
    *,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    environment: Mapping[str, str] | None = None,
) -> None:
    """执行已由调用方确认的计划；不使用 shell。"""

    for step in steps:
        print(f"开始安装：{step.label}")
        arguments: dict[str, object] = {
            "input": step.input_text,
            "text": True,
            "check": True,
        }
        if environment is not None or step.environment is not None:
            run_environment = dict(os.environ)
            if environment is not None:
                run_environment.update(environment)
            if step.environment is not None:
                run_environment.update(step.environment)
            arguments["env"] = run_environment
        runner(list(step.command), **arguments)
