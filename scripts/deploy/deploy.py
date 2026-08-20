#!/usr/bin/env python3
"""Windows/Linux 共用的部署控制面。

默认仅做本地预检和制品构建。明确传入 ``--apply`` 后，本模块使用本机 OpenSSH
上传受限制品和目标端操作层；所有 Linux 变更只会在远端
``scripts/deploy/remote/linux`` 及其既有受审计后端中执行。
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from stat import S_IMODE
from typing import Final, Iterable

try:  # 允许作为 ``python scripts/deploy/deploy.py`` 或模块导入运行。
    from .inventory import DeploymentInventory, InventoryError, load_inventory
    from .package import Artifact, PackageError, build_artifact
    from .preflight import PreflightReport, run_preflight
except ImportError:  # pragma: no cover - 直接脚本执行路径。
    from inventory import DeploymentInventory, InventoryError, load_inventory
    from package import Artifact, PackageError, build_artifact
    from preflight import PreflightReport, run_preflight


class DeployError(RuntimeError):
    """跨平台控制面无法安全继续。"""


_CONTROL_SOURCE_PATHS: Final = (Path("scripts/deploy"), Path("infra/systemd"))
_RUNTIME_PATH_KEYS: Final = (
    "RUNTIME_STORAGE_DIR",
    "RUNTIME_DOWNLOADS_DIR",
    "RUNTIME_REPORTS_DIR",
    "RUNTIME_LOG_DIR",
    "RUNTIME_CACHE_DIR",
    "RUNTIME_MATPLOTLIB_DIR",
)
_NTFY_KEYS: Final = (
    "NTFY_DEPLOY_ENABLED",
    "NTFY_PUBLIC_HOST",
    "NTFY_ACME_EMAIL",
    "NTFY_IMAGE",
    "NTFY_CADDY_IMAGE",
    "NTFY_CONFIG_DIR",
    "NTFY_DATA_DIR",
    "NTFY_CACHE_DURATION",
)
_CONTROL_EXCLUDED_DIRS: Final = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache"})
_CONTROL_EXCLUDED_SUFFIXES: Final = frozenset({".pyc", ".pyo"})
_UV_VERSION_PATTERN: Final = re.compile(r"^uv\s+([0-9][0-9A-Za-z.+-]*)(?:\s+.*)?$")


@dataclass(frozen=True)
class _RemotePaths:
    work_dir: str
    control_archive: str
    artifact: str
    active_env: str
    ntfy_bootstrap: str
    target_script: str


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_from_project(project_root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _print_report(report: PreflightReport) -> None:
    for check in report.checks:
        print(f"通过：{check}")
    for warning in report.warnings:
        print(f"警告：{warning}")
    for error in report.errors:
        print(f"失败：{error}")


def _control_path_is_excluded(relative_path: Path) -> bool:
    if any(part in _CONTROL_EXCLUDED_DIRS for part in relative_path.parts):
        return True
    if relative_path.suffix in _CONTROL_EXCLUDED_SUFFIXES:
        return True
    name = relative_path.name
    return name == ".env" or name.endswith(".env") or ".env." in name


def _iter_control_paths(project_root: Path) -> Iterable[Path]:
    for relative_root in _CONTROL_SOURCE_PATHS:
        source = project_root / relative_root
        if not source.is_dir() or source.is_symlink():
            raise DeployError(f"部署控制面缺少受信任目录：{relative_root}")
        yield source
        for candidate in sorted(source.rglob("*")):
            relative_path = candidate.relative_to(project_root)
            if _control_path_is_excluded(relative_path):
                continue
            if candidate.is_symlink():
                raise DeployError(f"部署控制面不允许包含符号链接：{relative_path}")
            if candidate.is_dir() or candidate.is_file():
                yield candidate
            else:
                raise DeployError(f"部署控制面包含不受支持的文件类型：{relative_path}")


def _add_control_path(archive: tarfile.TarFile, project_root: Path, path: Path) -> None:
    relative_path = path.relative_to(project_root)
    stat_result = path.stat()
    info = tarfile.TarInfo(relative_path.as_posix())
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = S_IMODE(stat_result.st_mode)
    info.mtime = int(stat_result.st_mtime)
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        info.size = 0
        archive.addfile(info)
        return
    info.size = stat_result.st_size
    with path.open("rb") as source:
        archive.addfile(info, source)


def _build_control_archive(project_root: Path, destination: Path) -> Path:
    """构建仅含 Linux 目标后端和 systemd 模板的临时传输包。"""

    try:
        with tarfile.open(destination, mode="x:gz", format=tarfile.PAX_FORMAT) as archive:
            for path in _iter_control_paths(project_root):
                _add_control_path(archive, project_root, path)
    except DeployError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, tarfile.TarError) as exc:
        destination.unlink(missing_ok=True)
        raise DeployError(f"无法构建 Linux 目标操作包：{exc}") from exc
    destination.chmod(0o600)
    return destination


def _run_quality_gates(*, project_root: Path, skip_ruff: bool, skip_tests: bool) -> None:
    """实际部署前运行与既有后端一致的本地质量门禁。"""

    uv = shutil.which("uv")
    if uv is None:
        raise DeployError("实际部署需要 uv。")
    if not skip_ruff:
        print("运行 Ruff")
        if subprocess.run([uv, "run", "ruff", "check", "."], cwd=project_root).returncode:
            raise DeployError("Ruff 未通过，拒绝继续部署。")
    if not skip_tests:
        print("运行 Pytest")
        if subprocess.run([uv, "run", "pytest"], cwd=project_root).returncode:
            raise DeployError("Pytest 未通过，拒绝继续部署。")


def _local_uv_version() -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise DeployError("未找到 uv。")
    result = subprocess.run([uv, "--version"], check=False, capture_output=True, text=True)
    match = _UV_VERSION_PATTERN.fullmatch(result.stdout.strip())
    if result.returncode != 0 or match is None:
        raise DeployError("无法识别本机 uv 版本。")
    return match.group(1)


def _run_remote_command(
    *,
    ssh: str,
    host: str,
    command: str,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [ssh, "-o", "BatchMode=yes", host, command],
        check=False,
        capture_output=capture_output,
        text=capture_output,
    )
    if check and result.returncode != 0:
        raise DeployError(f"远程命令失败（退出码 {result.returncode}）。")
    return result


def _copy_to_remote(*, scp: str, host: str, local_path: Path, remote_path: str) -> None:
    result = subprocess.run(
        [scp, "-o", "BatchMode=yes", str(local_path), f"{host}:{remote_path}"],
        check=False,
    )
    if result.returncode != 0:
        raise DeployError(f"上传文件失败：{local_path.name}（退出码 {result.returncode}）。")


def _assert_linux_target(*, ssh: str, host: str) -> None:
    print("检查远程 Linux 与非交互 sudo")
    platform = _run_remote_command(
        ssh=ssh,
        host=host,
        command="uname -s",
        capture_output=True,
    ).stdout.strip()
    if platform != "Linux":
        raise DeployError(f"远程目标不是 Linux：{host}")
    _run_remote_command(ssh=ssh, host=host, command="sudo -n true")


def _remote_paths(inventory: DeploymentInventory, artifact: Artifact, setup_server: bool) -> _RemotePaths:
    work_dir = f"{inventory.remote_tmp.rstrip('/')}/{inventory.app_name}-deploy-{artifact.release_id}"
    target_name = "install.sh" if setup_server else "upgrade.sh"
    return _RemotePaths(
        work_dir=work_dir,
        control_archive=f"{work_dir}/linux-control.tar.gz",
        artifact=f"{work_dir}/{artifact.path.name}",
        active_env=f"{work_dir}/active.env",
        ntfy_bootstrap=f"{work_dir}/private-ntfy.bootstrap.env",
        target_script=f"{work_dir}/scripts/deploy/remote/linux/{target_name}",
    )


def _remote_environment_command(
    *,
    inventory: DeploymentInventory,
    remote_paths: _RemotePaths,
    artifact: Artifact,
    uv_version: str,
    setup_server: bool,
    upload_env: bool,
    upload_ntfy_bootstrap: bool,
    confirm_live_deploy: str,
) -> str:
    values = inventory.values
    environment: dict[str, str] = {
        "APP_NAME": inventory.app_name,
        "SERVICE_USER": inventory.service_user,
        "SERVICE_HOME": inventory.service_home,
        "SYSTEMD_SERVICE_NAME": inventory.systemd_service_name,
        "SERVICE_MODE": inventory.service_mode,
        "PYTHON_VERSION": inventory.python_version,
        "UV_VERSION": uv_version,
        "KEEP_RELEASES": str(inventory.keep_releases),
        "SETUP_SERVER": "1" if setup_server else "0",
        "CONFIRM_LIVE_DEPLOY": confirm_live_deploy,
        "ARTIFACT_TARBALL": remote_paths.artifact,
        "ARTIFACT_SHA256": artifact.sha256,
        "RELEASE_ID": artifact.release_id,
        "DASHBOARD_DEPLOY_ENABLED": "1" if inventory.dashboard_deploy_enabled else "0",
        "UPLOAD_NTFY_BOOTSTRAP": "1" if upload_ntfy_bootstrap else "0",
    }
    for key in _RUNTIME_PATH_KEYS + _NTFY_KEYS:
        environment[key] = values.get(key, "")
    if upload_env:
        environment["ENV_FILE_PATH"] = remote_paths.active_env
    if upload_ntfy_bootstrap:
        environment["NTFY_BOOTSTRAP_PATH"] = remote_paths.ntfy_bootstrap

    assignments = " ".join(f"{key}={shlex.quote(value)}" for key, value in environment.items())
    return f"sudo -n env {assignments} bash {shlex.quote(remote_paths.target_script)}"


def _validate_ntfy_bootstrap(
    *,
    args: argparse.Namespace,
    inventory: DeploymentInventory,
    project_root: Path,
) -> Path | None:
    if args.ntfy_bootstrap_file is not None and not args.upload_ntfy_bootstrap:
        raise DeployError("--ntfy-bootstrap-file 必须与 --upload-ntfy-bootstrap 一起使用。")
    if not args.upload_ntfy_bootstrap:
        return None
    if not args.upload_env:
        raise DeployError("--upload-ntfy-bootstrap 必须同时使用 --upload-env。")
    if not inventory.ntfy_deploy_enabled:
        raise DeployError("--upload-ntfy-bootstrap 要求部署清单设置 NTFY_DEPLOY_ENABLED=1。")
    if args.confirm_ntfy_bootstrap != "YES":
        raise DeployError("上传私有 ntfy bootstrap 前必须明确传入 --confirm-ntfy-bootstrap YES。")
    requested = args.ntfy_bootstrap_file or Path("ntfy.bootstrap.env")
    bootstrap_file = _resolve_from_project(project_root, requested)
    if not bootstrap_file.is_file() or bootstrap_file.is_symlink():
        raise DeployError("未找到可安全上传的私有 ntfy bootstrap 文件。")
    return bootstrap_file


def _deploy_to_linux(
    *,
    project_root: Path,
    inventory: DeploymentInventory,
    artifact: Artifact,
    env_file: Path | None,
    ntfy_bootstrap_file: Path | None,
    args: argparse.Namespace,
) -> None:
    """通过 Windows/Linux 都可用的 OpenSSH 客户端编排 Linux 目标端操作。"""

    ssh = shutil.which("ssh")
    scp = shutil.which("scp")
    if ssh is None or scp is None:
        raise DeployError("实际部署需要本机 OpenSSH 的 ssh 与 scp。")
    uv_version = _local_uv_version()
    remote_paths = _remote_paths(inventory, artifact, args.setup_server)
    cleanup_required = not args.keep_remote_staging or ntfy_bootstrap_file is not None
    staged = False

    with tempfile.TemporaryDirectory(prefix="northstar-linux-control-") as temporary_dir:
        control_archive = _build_control_archive(
            project_root, Path(temporary_dir) / "linux-control.tar.gz"
        )
        _assert_linux_target(ssh=ssh, host=inventory.deploy_host)
        try:
            print("创建受限远端暂存目录")
            _run_remote_command(
                ssh=ssh,
                host=inventory.deploy_host,
                command=(
                    f"test ! -e {shlex.quote(remote_paths.work_dir)} && "
                    "umask 077 && install -d -m 0700 -- "
                    f"{shlex.quote(remote_paths.work_dir)}"
                ),
            )
            staged = True

            print("上传 Linux 目标操作层和 systemd 模板")
            _copy_to_remote(
                scp=scp,
                host=inventory.deploy_host,
                local_path=control_archive,
                remote_path=remote_paths.control_archive,
            )
            _run_remote_command(
                ssh=ssh,
                host=inventory.deploy_host,
                command=(
                    f"tar -xzf {shlex.quote(remote_paths.control_archive)} "
                    f"-C {shlex.quote(remote_paths.work_dir)} && "
                    f"rm -f -- {shlex.quote(remote_paths.control_archive)}"
                ),
            )

            print("上传应用制品")
            _copy_to_remote(
                scp=scp,
                host=inventory.deploy_host,
                local_path=artifact.path,
                remote_path=remote_paths.artifact,
            )
            if env_file is not None:
                print("上传活动 .env")
                _copy_to_remote(
                    scp=scp,
                    host=inventory.deploy_host,
                    local_path=env_file,
                    remote_path=remote_paths.active_env,
                )
                _run_remote_command(
                    ssh=ssh,
                    host=inventory.deploy_host,
                    command=f"chmod 600 -- {shlex.quote(remote_paths.active_env)}",
                )
            if ntfy_bootstrap_file is not None:
                print("上传仅供本次初始化使用的私有 ntfy bootstrap 文件")
                _copy_to_remote(
                    scp=scp,
                    host=inventory.deploy_host,
                    local_path=ntfy_bootstrap_file,
                    remote_path=remote_paths.ntfy_bootstrap,
                )
                _run_remote_command(
                    ssh=ssh,
                    host=inventory.deploy_host,
                    command=f"chmod 600 -- {shlex.quote(remote_paths.ntfy_bootstrap)}",
                )

            print("执行 Linux 目标端安装/升级")
            _run_remote_command(
                ssh=ssh,
                host=inventory.deploy_host,
                command=_remote_environment_command(
                    inventory=inventory,
                    remote_paths=remote_paths,
                    artifact=artifact,
                    uv_version=uv_version,
                    setup_server=args.setup_server,
                    upload_env=env_file is not None,
                    upload_ntfy_bootstrap=ntfy_bootstrap_file is not None,
                    confirm_live_deploy=args.confirm_live_deploy,
                ),
            )
        finally:
            if staged and cleanup_required:
                cleanup = _run_remote_command(
                    ssh=ssh,
                    host=inventory.deploy_host,
                    command=f"rm -rf -- {shlex.quote(remote_paths.work_dir)}",
                    check=False,
                )
                if cleanup.returncode != 0:
                    print("警告：无法清理远端暂存目录；请人工检查受限临时路径。")
            elif staged:
                print(f"保留远端暂存目录：{remote_paths.work_dir}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Northstar Quant 跨平台部署入口（默认 dry-run，不连接服务器）。",
        epilog=(
            "首次部署到 Linux 服务器可通过 scripts/deploy.sh 调用，并需显式使用 "
            "--apply --setup-server --upload-env；真实交易 scheduler 还必须传入 "
            "--confirm-live-deploy YES。"
        ),
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("deploy.env"),
        help="非机密 Linux 目标清单，默认 deploy.env。",
    )
    parser.add_argument("--project-root", type=Path, help="项目根目录，默认自动推导。")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"), help="制品输出目录。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="明确允许使用 OpenSSH 调用 Linux 目标操作层；默认只演练。",
    )
    mode.add_argument("--dry-run", action="store_true", help="显式声明仅演练（默认行为）。")
    parser.add_argument("--upload-env", action="store_true", help="上传前校验唯一活动 .env。")
    parser.add_argument("--env-file", type=Path, help="活动环境文件；默认项目根目录 .env。")
    parser.add_argument("--setup-server", action="store_true", help="仅首次 Linux 安装时使用。")
    parser.add_argument("--allow-dirty", action="store_true", help="明确允许未提交工作区。")
    parser.add_argument("--skip-tests", action="store_true", help="跳过实际部署前的完整 pytest。")
    parser.add_argument("--skip-ruff", action="store_true", help="跳过实际部署前的 Ruff。")
    parser.add_argument(
        "--confirm-live-deploy",
        choices=("NO", "YES"),
        default="NO",
        help="只有经人工确认的非 paper scheduler 才能使用 YES。",
    )
    parser.add_argument(
        "--upload-ntfy-bootstrap",
        action="store_true",
        help="显式上传仅供本次初始化的私有 ntfy bootstrap 文件。",
    )
    parser.add_argument(
        "--ntfy-bootstrap-file",
        type=Path,
        help="私有 ntfy bootstrap 文件；默认项目根目录 ntfy.bootstrap.env。",
    )
    parser.add_argument(
        "--confirm-ntfy-bootstrap",
        choices=("NO", "YES"),
        default="NO",
        help="确认允许上传一次性 ntfy 身份 bootstrap。",
    )
    parser.add_argument(
        "--keep-remote-staging",
        action="store_true",
        help="保留远端暂存目录用于排障；含 ntfy bootstrap 时仍会强制清理。",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    project_root = (args.project_root or _default_project_root()).resolve()
    inventory_path = _resolve_from_project(project_root, args.inventory)
    if args.env_file is not None and not args.upload_env:
        print("部署参数错误：--env-file 必须与 --upload-env 一起使用。")
        return 2
    if args.setup_server and not args.apply:
        print("部署参数错误：--setup-server 只能与 --apply 一起使用。")
        return 2
    if args.upload_ntfy_bootstrap and not args.apply:
        print("部署参数错误：--upload-ntfy-bootstrap 只能与 --apply 一起使用。")
        return 2

    env_file: Path | None = None
    if args.upload_env:
        requested_env_file = args.env_file or Path(".env")
        env_file = _resolve_from_project(project_root, requested_env_file)
        if env_file.name != ".env":
            print("部署参数错误：活动环境文件必须命名为 .env，不能维护第二套生产配置。")
            return 2

    try:
        inventory = load_inventory(inventory_path)
        ntfy_bootstrap_file = _validate_ntfy_bootstrap(
            args=args,
            inventory=inventory,
            project_root=project_root,
        )
    except (DeployError, InventoryError) as exc:
        print(f"部署清单或参数校验失败：{exc}")
        return 1

    report = run_preflight(
        project_root=project_root,
        inventory=inventory,
        upload_env=args.upload_env,
        env_file=env_file,
        allow_dirty=args.allow_dirty,
        apply=args.apply,
        confirm_live_deploy=args.confirm_live_deploy,
    )
    _print_report(report)
    if not report.passed:
        return 1

    try:
        if args.apply:
            _run_quality_gates(
                project_root=project_root,
                skip_ruff=args.skip_ruff,
                skip_tests=args.skip_tests,
            )
        artifact = build_artifact(
            project_root=project_root,
            output_dir=_resolve_from_project(project_root, args.output_dir),
        )
        print(f"制品={artifact.path}")
        print(f"SHA256={artifact.sha256}")
        if not args.apply:
            print("dry-run 完成：未连接服务器，未执行 Linux 目标操作。")
            return 0
        _deploy_to_linux(
            project_root=project_root,
            inventory=inventory,
            artifact=artifact,
            env_file=env_file,
            ntfy_bootstrap_file=ntfy_bootstrap_file,
            args=args,
        )
    except (DeployError, PackageError) as exc:
        print(f"部署失败：{exc}")
        return 1

    print("部署完成")
    print(f"host={inventory.deploy_host}")
    print(f"release={artifact.release_id}")
    print(f"service={inventory.systemd_service_name}.service")
    print(f"mode={inventory.service_mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
