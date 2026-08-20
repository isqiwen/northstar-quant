"""跨平台开发工具安装计划。

默认只生成可审阅的命令，不执行系统安装。调用方必须在收到明确的 ``YES``
确认后才可以执行计划；本模块不会使用 shell、不会启动 Docker 服务，也不会修改
Docker 用户组。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import platform
import re
import shlex
import stat
import subprocess
import sys


OS_RELEASE_PATH = Path("/etc/os-release")
DOCKER_APT_SOURCE = Path("/etc/apt/sources.list.d/docker.sources")
DOCKER_APT_KEYRING = Path("/etc/apt/keyrings/docker.asc")
_SAFE_OS_RELEASE_VALUE = re.compile(r"^[a-z0-9._-]+$")


class BootstrapPlanError(RuntimeError):
    """表示无法安全生成当前主机的安装计划。"""


class DockerRepositoryState(str, Enum):
    """Docker APT repository 的可审计状态。

    ``RECOVERABLE_SOURCE_ONLY`` 是本安装器采用 source-first 顺序时唯一允许
    自动恢复的半完成状态：源内容必须严格匹配本次发行版，且 keyring 完全不存在。
    其他不完整或未知状态均为 ``CONFLICT``，由调用方 fail-closed。
    """

    ABSENT = "absent"
    COMPATIBLE = "compatible"
    RECOVERABLE_SOURCE_ONLY = "recoverable_source_only"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class DockerRepositoryInspection:
    """Docker APT repository 检查结果，不会写入系统文件。"""

    state: DockerRepositoryState
    conflict_reason: str | None = None
    keyring_needs_read_permission: bool = False


@dataclass(frozen=True)
class InstallStep:
    """一个不经 shell 展开的、可审阅的系统安装步骤。"""

    label: str
    command: tuple[str, ...]
    input_text: str | None = None
    note: str | None = None


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


def _windows_plan(*, missing_tools: set[str], install_docker: bool) -> list[InstallStep]:
    package_ids = {
        "uv": "astral-sh.uv",
        "just": "Casey.Just",
        "git": "Git.Git",
    }
    if install_docker:
        package_ids["docker"] = "Docker.DockerDesktop"
        package_ids["docker-compose-v2"] = "Docker.DockerDesktop"

    selected_ids: list[str] = []
    for tool, package_id in package_ids.items():
        if tool in missing_tools and package_id not in selected_ids:
            selected_ids.append(package_id)
    if not selected_ids:
        return []

    steps = [
        InstallStep(
            label=f"通过 winget 安装 {package_id}",
            command=("winget", "install", "--id", package_id, "--exact", "--source", "winget"),
            note=(
                "Docker Desktop 的许可、WSL 2 配置和首次启动必须由操作者自行确认；"
                "本脚本不会接受许可或启动服务。"
                if package_id == "Docker.DockerDesktop"
                else None
            ),
        )
        for package_id in selected_ids
    ]
    return steps


def _docker_source_contents(*, release: dict[str, str], keyring: Path) -> str:
    """生成唯一受支持的 Docker deb822 源声明。"""

    return "\n".join(
        (
            "Types: deb",
            f"URIs: https://download.docker.com/linux/{release['id']}",
            f"Suites: {release['codename']}",
            "Components: stable",
            f"Signed-By: {keyring}",
            "",
        )
    )


def _regular_file_mode(path: Path, *, label: str) -> tuple[int | None, str | None]:
    """检查文件是否可安全复用，返回 mode 或不安全原因。

    使用 ``lstat`` 而不是 ``exists``/``is_file``，从而连断开的符号链接也会被
    明确视为冲突，而不是误判为缺失后被后续 root 命令覆盖。
    """

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None, None
    except OSError as error:
        return None, f"无法检查 {label}，已拒绝继续 Docker 安装：{error}"
    if stat.S_ISLNK(mode):
        return None, f"{label} 不能是符号链接，已拒绝复用或覆盖。"
    if not stat.S_ISREG(mode):
        return None, f"{label} 必须是普通文件，已拒绝复用或覆盖。"
    return mode, None


def _docker_repository_state(
    *,
    source_path: Path,
    keyring_path: Path,
    expected_source: str,
) -> DockerRepositoryInspection:
    """检查 Docker APT 源/keyring 状态，且不修改现有系统配置。

    状态矩阵：

    * ``absent``：source 与 keyring 均不存在，可在经确认执行时创建；
    * ``compatible``：source 严格匹配，keyring 是普通文件，可复用；
    * ``recoverable_source_only``：严格匹配的 source 存在、keyring 不存在；
      这是 source-first 中断时唯一可验证的恢复路径，只补 keyring；
    * ``conflict``：keyring-only、内容不匹配、链接、非普通文件或无法读取；
      调用方必须 fail-closed，不能覆盖或继续。
    """

    source_mode, source_error = _regular_file_mode(source_path, label="Docker APT 源")
    if source_error:
        return DockerRepositoryInspection(
            DockerRepositoryState.CONFLICT,
            conflict_reason=source_error,
        )
    keyring_mode, keyring_error = _regular_file_mode(keyring_path, label="Docker APT 签名密钥")
    if keyring_error:
        return DockerRepositoryInspection(
            DockerRepositoryState.CONFLICT,
            conflict_reason=keyring_error,
        )

    if source_mode is None:
        if keyring_mode is not None:
            return DockerRepositoryInspection(
                DockerRepositoryState.CONFLICT,
                conflict_reason=(
                    "检测到 Docker APT 签名密钥但没有匹配的 Docker APT 源；"
                    "无法安全判定其来源，已拒绝覆盖或继续。"
                ),
            )
        return DockerRepositoryInspection(DockerRepositoryState.ABSENT)

    try:
        actual_source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        return DockerRepositoryInspection(
            DockerRepositoryState.CONFLICT,
            conflict_reason=f"无法读取既有 Docker APT 源，已拒绝继续 Docker 安装：{error}",
        )
    if actual_source.strip() != expected_source.strip():
        return DockerRepositoryInspection(
            DockerRepositoryState.CONFLICT,
            conflict_reason=(
                "检测到既有 Docker APT 源，但内容不是本工具可验证的目标状态；"
                "为避免覆盖系统配置，已拒绝继续。"
            ),
        )
    if keyring_mode is None:
        return DockerRepositoryInspection(DockerRepositoryState.RECOVERABLE_SOURCE_ONLY)

    return DockerRepositoryInspection(
        DockerRepositoryState.COMPATIBLE,
        keyring_needs_read_permission=not bool(stat.S_IMODE(keyring_mode) & stat.S_IROTH),
    )


def _linux_plan(
    *,
    missing_tools: set[str],
    install_docker: bool,
    os_release_path: Path,
    docker_apt_source: Path,
    docker_apt_keyring: Path,
) -> list[InstallStep]:
    release = _read_os_release(os_release_path)
    steps: list[InstallStep] = []

    packages = [tool for tool in ("git", "just") if tool in missing_tools]
    if packages:
        steps.extend(
            (
                InstallStep("更新 APT 软件包索引", ("sudo", "apt-get", "update")),
                InstallStep(
                    "安装基础开发工具",
                    ("sudo", "apt-get", "install", "--yes", *packages),
                ),
            )
        )

    if "uv" in missing_tools:
        steps.append(
            InstallStep(
                "通过当前 Python 安装 uv",
                (sys.executable, "-m", "pip", "install", "--user", "uv"),
                note="需要已存在的 Python >= 3.11；不会安装或替换 Python。",
            )
        )

    docker_missing = {"docker", "docker-compose-v2"} & missing_tools
    if install_docker and docker_missing:
        source_contents = _docker_source_contents(
            release=release,
            keyring=docker_apt_keyring,
        )
        repository = _docker_repository_state(
            source_path=docker_apt_source,
            keyring_path=docker_apt_keyring,
            expected_source=source_contents,
        )
        if repository.state is DockerRepositoryState.CONFLICT:
            raise BootstrapPlanError(repository.conflict_reason or "Docker APT 配置状态冲突。")

        source_exists = repository.state is not DockerRepositoryState.ABSENT
        keyring_exists = repository.state is DockerRepositoryState.COMPATIBLE
        keyring_needs_read_permission = repository.keyring_needs_read_permission
        repository_note = {
            DockerRepositoryState.ABSENT: (
                "Docker APT 源与签名密钥均不存在；经确认执行时将创建两者。"
            ),
            DockerRepositoryState.RECOVERABLE_SOURCE_ONLY: (
                "检测到严格匹配的 Docker APT 源但 keyring 缺失；"
                "这是 source-first 中断后的可恢复状态，只补齐 keyring，不改写源。"
            ),
            DockerRepositoryState.COMPATIBLE: (
                "Docker APT 源已严格匹配当前发行版，将复用既有普通文件 keyring，"
                "不改写仓库配置。"
            ),
        }[repository.state]

        # 新机器先装下载 key 所需的基础工具。严格匹配的 source-only 状态仅作为
        # source-first 中断恢复接受；它不再运行 APT update，因为该源暂时指向缺失
        # keyring，而下载工具已在 source 写入前的步骤中安装。
        if not keyring_exists and not source_exists:
            steps.extend(
                (
                    InstallStep(
                        "更新 Docker 前的 APT 软件包索引",
                        ("sudo", "apt-get", "update"),
                        note=repository_note,
                    ),
                    InstallStep(
                        "安装 Docker 仓库前置工具",
                        ("sudo", "apt-get", "install", "--yes", "ca-certificates", "curl"),
                    ),
                )
            )

        # source 在 keyring 之前写入，令任意可恢复的半完成状态都有严格可验证的
        # source 内容；APT 更新只在 keyring 写好后发生。
        if not source_exists:
            steps.extend(
                (
                    InstallStep(
                        "创建 Docker APT 源目录",
                        ("sudo", "install", "-m", "0755", "-d", str(docker_apt_source.parent)),
                    ),
                    InstallStep(
                        "写入 Docker APT 源",
                        ("sudo", "tee", str(docker_apt_source)),
                        input_text=source_contents,
                    ),
                )
            )

        if not keyring_exists:
            steps.extend(
                (
                    InstallStep(
                        "创建 Docker APT keyring 目录",
                        ("sudo", "install", "-m", "0755", "-d", str(docker_apt_keyring.parent)),
                    ),
                    InstallStep(
                        "下载 Docker APT 签名密钥",
                        (
                            "sudo",
                            "curl",
                            "-fsSL",
                            f"https://download.docker.com/linux/{release['id']}/gpg",
                            "-o",
                            str(docker_apt_keyring),
                        ),
                    ),
                    InstallStep(
                        "设置 Docker APT 签名密钥权限",
                        ("sudo", "chmod", "a+r", str(docker_apt_keyring)),
                    ),
                )
            )
        elif keyring_needs_read_permission:
            # 若先前在下载与 chmod 之间中断，安全地补齐只读权限；重复 chmod 的
            # 文件状态不变，因此不会破坏幂等性。
            steps.append(
                InstallStep(
                    "恢复 Docker APT 签名密钥读取权限",
                    ("sudo", "chmod", "a+r", str(docker_apt_keyring)),
                )
            )

        steps.extend(
            (
                InstallStep(
                    "更新 Docker APT 软件包索引",
                    ("sudo", "apt-get", "update"),
                    note=(
                        repository_note
                        if repository.state is not DockerRepositoryState.ABSENT
                        else None
                    ),
                ),
                InstallStep(
                    "安装 Docker Engine 与 Compose v2",
                    (
                        "sudo",
                        "apt-get",
                        "install",
                        "--yes",
                        "docker-ce",
                        "docker-ce-cli",
                        "containerd.io",
                        "docker-buildx-plugin",
                        "docker-compose-plugin",
                    ),
                    note="不会执行 systemctl、Docker 命令或 usermod；服务状态与访问权限请自行确认。",
                ),
            )
        )
    return steps


def build_install_plan(
    *,
    missing_tools: Iterable[str],
    install_docker: bool,
    system_name: str | None = None,
    os_release_path: Path = OS_RELEASE_PATH,
    docker_apt_source: Path = DOCKER_APT_SOURCE,
    docker_apt_keyring: Path = DOCKER_APT_KEYRING,
) -> list[InstallStep]:
    """为缺失工具生成安装计划，绝不执行命令。

    ``install_docker`` 为 false 时，即使 Docker 缺失也绝不会出现在计划中。
    """

    missing = set(missing_tools)
    current_system = system_name or platform.system()
    if current_system == "Windows":
        return _windows_plan(missing_tools=missing, install_docker=install_docker)
    if current_system == "Linux":
        return _linux_plan(
            missing_tools=missing,
            install_docker=install_docker,
            os_release_path=os_release_path,
            docker_apt_source=docker_apt_source,
            docker_apt_keyring=docker_apt_keyring,
        )
    raise BootstrapPlanError(
        "开发工具 bootstrap 仅正式支持 Windows 与 Ubuntu/Debian Linux；当前平台只提供检查。"
    )


def execute_install_plan(
    steps: Iterable[InstallStep],
    *,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> None:
    """执行已由调用方确认的计划；不使用 shell。"""

    for step in steps:
        print(f"开始安装：{step.label}")
        runner(
            list(step.command),
            input=step.input_text,
            text=True,
            check=True,
        )
