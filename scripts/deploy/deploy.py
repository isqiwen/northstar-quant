#!/usr/bin/env python3
"""Windows/Linux 共用的部署控制面。

默认仅做本地预检和制品构建。明确传入 ``--apply`` 后，本模块使用本机 OpenSSH
上传受限制品和目标端操作层；所有 Linux 变更只会在远端
``scripts/deploy/remote/linux`` 及其既有受审计后端中执行。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from northstar_quant.foundation.security import SecurityAuditEvent

try:  # 允许作为 ``python scripts/deploy/deploy.py`` 或模块导入运行。
    from .control_bundle import ControlBundleError, build_control_artifact
    from .inventory import DeploymentInventory, InventoryError, load_inventory
    from .package import Artifact, PackageError, build_artifact
    from .preflight import PreflightReport, run_preflight
    from .release_manifest import (
        ReleaseManifestError,
        build_manifest,
        canonical_manifest_bytes,
    )
    from .release_signing import ReleaseSigningError, sign_environment, sign_manifest
    from .root_release_runner import (
        GATE_PROTOCOL,
        ROOT_RUNNER_PATH,
        RootReleaseRunnerError,
        Submission,
        write_submission,
    )
except ImportError:  # pragma: no cover - 直接脚本执行路径。
    from control_bundle import ControlBundleError, build_control_artifact
    from inventory import DeploymentInventory, InventoryError, load_inventory
    from package import Artifact, PackageError, build_artifact
    from preflight import PreflightReport, run_preflight
    from release_manifest import ReleaseManifestError, build_manifest, canonical_manifest_bytes
    from release_signing import ReleaseSigningError, sign_environment, sign_manifest
    from root_release_runner import (
        GATE_PROTOCOL,
        ROOT_RUNNER_PATH,
        RootReleaseRunnerError,
        Submission,
        write_submission,
    )


class DeployError(RuntimeError):
    """跨平台控制面无法安全继续。"""


_UV_VERSION_PATTERN: Final = re.compile(r"^uv\s+([0-9][0-9A-Za-z.+-]*)(?:\s+.*)?$")
_SSH_OPTIONS: Final = (
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=yes",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ConnectionAttempts=1",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=3",
)
_REMOTE_COMMAND_TIMEOUT_SECONDS: Final = 900
_RELEASE_GATE_TIMEOUT_SECONDS: Final = 60 * 60


@dataclass(frozen=True)
class GateIdentity:
    gate_identity: str
    gate_protocol: str


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


def _print_audit(*, action: str, outcome: str, subject: str, **details: object) -> None:
    event = SecurityAuditEvent(
        actor="deployment-control-plane",
        action=action,
        outcome=outcome,
        subject=subject,
        occurred_at=datetime.now(UTC),
        details=dict(details),
    )
    print("security_audit=" + event.to_json())


def _run_quality_gates(*, project_root: Path) -> None:
    """实际部署前运行不可跳过的本地质量门禁。"""

    uv = shutil.which("uv")
    if uv is None:
        raise DeployError("实际部署需要 uv。")
    quality_gates = (
        ("离线依赖策略", (sys.executable, "scripts/ci/check_dependency_policy.py")),
        ("离线 lock 检查", (uv, "lock", "--check", "--offline")),
        ("密钥扫描", (sys.executable, "scripts/ci/check_secrets.py")),
        ("Ruff", (uv, "run", "--offline", "--no-sync", "ruff", "check", ".")),
        (
            "mypy 基线",
            (uv, "run", "--offline", "--no-sync", "python", "scripts/ci/check_mypy_baseline.py", "check"),
        ),
        ("Pytest", (uv, "run", "--offline", "--no-sync", "pytest")),
    )
    for name, command in quality_gates:
        print(f"运行{name}")
        if subprocess.run(command, cwd=project_root).returncode:
            raise DeployError(f"{name} 未通过，拒绝继续部署。")


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
    try:
        result = subprocess.run(
            [ssh, *_SSH_OPTIONS, host, command],
            check=False,
            capture_output=capture_output,
            text=capture_output,
            timeout=_REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeployError("远程命令超时，拒绝继续部署。") from exc
    if check and result.returncode != 0:
        raise DeployError(f"远程命令失败（退出码 {result.returncode}）。")
    return result


def _assert_linux_target(*, ssh: str, inventory: DeploymentInventory) -> GateIdentity:
    """Verify the fixed Linux gate instead of granting generic remote sudo."""

    print("检查远程 Linux、独立部署身份和固定 release gate")
    platform = _run_remote_command(
        ssh=ssh,
        host=inventory.deploy_host,
        command="uname -s",
        capture_output=True,
    ).stdout.strip()
    if platform != "Linux":
        raise DeployError(f"远程目标不是 Linux：{inventory.deploy_host}")
    deploy_user = _run_remote_command(
        ssh=ssh,
        host=inventory.deploy_host,
        command="id -un",
        capture_output=True,
    ).stdout.strip()
    if deploy_user in {"", "root", inventory.service_user}:
        raise DeployError("远程 SSH 部署身份必须是非 root 且不同于 SERVICE_USER 的独立身份。")
    response = _run_remote_command(
        ssh=ssh,
        host=inventory.deploy_host,
        command=f"sudo -n {ROOT_RUNNER_PATH} identity",
        capture_output=True,
    ).stdout.strip()
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise DeployError("固定 root release gate 未返回可验证的身份声明。") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"gate_identity", "gate_protocol"}
        or not isinstance(payload["gate_identity"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload["gate_identity"])
        or payload["gate_protocol"] != GATE_PROTOCOL
    ):
        raise DeployError("固定 root release gate 身份声明不符合受信任协议。")
    return GateIdentity(gate_identity=payload["gate_identity"], gate_protocol=payload["gate_protocol"])


def _release_profile(
    *,
    inventory: DeploymentInventory,
    setup_server: bool,
    confirm_live_deploy: str,
    uv_version: str,
) -> dict[str, str]:
    """Build the complete, signed, non-secret profile accepted by the root gate."""

    if inventory.ntfy_deploy_enabled:
        raise DeployError(
            "NTFY_DEPLOY_ENABLED=1 is not accepted by the signed release gate; "
            "provision ntfy through its separate root-operated workflow."
        )
    values = inventory.values
    return {
        "app_name": inventory.app_name,
        "confirm_live_deploy": confirm_live_deploy,
        "dashboard_deploy_enabled": "1" if inventory.dashboard_deploy_enabled else "0",
        "keep_releases": str(inventory.keep_releases),
        "ntfy_deploy_enabled": "0",
        "python_version": inventory.python_version,
        "runtime_storage_dir": values.get("RUNTIME_STORAGE_DIR", "") or "/var/lib/northstar/storage",
        "runtime_downloads_dir": values.get("RUNTIME_DOWNLOADS_DIR", "") or "/var/lib/northstar/downloads",
        "runtime_reports_dir": values.get("RUNTIME_REPORTS_DIR", "") or "/var/lib/northstar/reports",
        "runtime_log_dir": values.get("RUNTIME_LOG_DIR", "") or "/var/log/northstar/app",
        "runtime_cache_dir": values.get("RUNTIME_CACHE_DIR", "") or "/var/cache/northstar/runtime",
        "runtime_matplotlib_dir": values.get("RUNTIME_MATPLOTLIB_DIR", "") or "/var/cache/northstar/matplotlib",
        "service_mode": inventory.service_mode,
        "service_user": inventory.service_user,
        "setup_server": "1" if setup_server else "0",
        "systemd_service_name": inventory.systemd_service_name,
        "uv_version": uv_version,
    }


def _submit_release_gate(*, ssh: str, host: str, submission: Submission) -> None:
    """Stream bytes to the only sudo verb; never stage paths or execute a remote shell."""

    command = [ssh, *_SSH_OPTIONS, host, f"sudo -n {ROOT_RUNNER_PATH} submit"]
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        if process.stdin is None:
            raise DeployError("cannot open the root release gate stdin stream")
        try:
            write_submission(process.stdin, submission)
        finally:
            process.stdin.close()
        return_code = process.wait(timeout=_RELEASE_GATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            process.terminate()
        raise DeployError(
            "release gate response timed out; remote transaction state is unknown and must be inspected manually"
        ) from exc
    except (BrokenPipeError, OSError, RootReleaseRunnerError) as exc:
        if process is not None:
            process.terminate()
        raise DeployError(
            "release gate transport failed; do not retry automatically because remote transaction state is unknown"
        ) from exc
    if return_code != 0:
        raise DeployError(
            "root release gate rejected the request; inspect its durable transaction before any manual recovery"
        )


def _deploy_to_linux(
    *,
    project_root: Path,
    inventory: DeploymentInventory,
    artifact: Artifact,
    env_file: Path | None,
    args: argparse.Namespace,
) -> None:
    """Submit one signed, immutable release to the fixed Linux root gate."""

    ssh = shutil.which("ssh")
    if ssh is None:
        raise DeployError("实际部署需要本机 OpenSSH ssh。")
    if not re.fullmatch(r"[0-9a-f]{40}", artifact.revision):
        raise DeployError("signed release artifact must carry a full committed Git revision")
    if args.signing_key is None:
        raise DeployError("--apply requires an explicit --signing-key for the release authority")

    identity = _assert_linux_target(ssh=ssh, inventory=inventory)
    profile = _release_profile(
        inventory=inventory,
        setup_server=args.setup_server,
        confirm_live_deploy=args.confirm_live_deploy,
        uv_version=_local_uv_version(),
    )
    with tempfile.TemporaryDirectory(prefix="northstar-release-control-") as temporary_dir:
        control = build_control_artifact(
            project_root=project_root,
            output_dir=Path(temporary_dir),
            release_id=artifact.release_id,
        )
        manifest = build_manifest(
            release_id=artifact.release_id,
            revision=artifact.revision,
            gate_identity=identity.gate_identity,
            profile=profile,
            environment_upload=env_file is not None,
            runtime_bundle=artifact.path,
            control_bundle=control.path,
        )
        manifest_bytes = canonical_manifest_bytes(manifest)
        signature = sign_manifest(manifest_bytes=manifest_bytes, signing_key=args.signing_key)
        environment_signature = (
            None
            if env_file is None
            else sign_environment(
                release_id=artifact.release_id,
                environment_path=env_file,
                signing_key=args.signing_key,
            )
        )
        print("streaming signed release bytes to the fixed root gate")
        _submit_release_gate(
            ssh=ssh,
            host=inventory.deploy_host,
            submission=Submission(
                manifest=manifest_bytes,
                signature=signature,
                runtime_path=artifact.path,
                control_path=control.path,
                environment_path=env_file,
                environment_signature=environment_signature,
            ),
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Northstar Quant 跨平台部署入口（默认 dry-run，不连接服务器）。",
        epilog=(
            "首次部署到 Linux 服务器需显式使用 --apply --setup-server --upload-env；"
            "真实交易 scheduler 还必须传入 --confirm-live-deploy YES。"
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
    parser.add_argument(
        "--signing-key",
        type=Path,
        help="仅 --apply 使用；操作员持有的 OpenSSH release-authority 私钥。",
    )
    parser.add_argument(
        "--confirm-live-deploy",
        choices=("NO", "YES"),
        default="NO",
        help="只有经人工确认的非 paper scheduler 才能使用 YES。",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    project_root = (args.project_root or _default_project_root()).resolve()
    inventory_path = _resolve_from_project(project_root, args.inventory)
    if args.env_file is not None and not args.upload_env:
        _print_audit(
            action="deploy",
            outcome="denied",
            subject=args.inventory.name,
            reason="--env-file requires --upload-env",
        )
        print("部署参数错误：--env-file 必须与 --upload-env 一起使用。")
        return 2
    if args.setup_server and not args.apply:
        _print_audit(
            action="deploy",
            outcome="denied",
            subject=args.inventory.name,
            reason="--setup-server requires --apply",
        )
        print("部署参数错误：--setup-server 只能与 --apply 一起使用。")
        return 2
    if args.apply and args.signing_key is None:
        _print_audit(
            action="deploy",
            outcome="denied",
            subject=args.inventory.name,
            reason="--apply requires --signing-key",
        )
        print("部署参数错误：--apply 必须提供操作员持有的 --signing-key。")
        return 2

    env_file: Path | None = None
    if args.upload_env:
        requested_env_file = args.env_file or Path(".env")
        env_file = _resolve_from_project(project_root, requested_env_file)
        if env_file.name != ".env":
            _print_audit(
                action="deploy",
                outcome="denied",
                subject=args.inventory.name,
                reason="active environment file must be named .env",
            )
            print("部署参数错误：活动环境文件必须命名为 .env，不能维护第二套生产配置。")
            return 2

    try:
        inventory = load_inventory(inventory_path)
        if inventory.ntfy_deploy_enabled:
            raise DeployError(
                "NTFY_DEPLOY_ENABLED=1 is not supported by the signed root release gate; "
                "use the separate root-operated ntfy workflow."
            )
    except (DeployError, InventoryError) as exc:
        _print_audit(
            action="deploy",
            outcome="denied",
            subject=args.inventory.name,
            reason=str(exc),
        )
        print(f"部署清单或参数校验失败：{exc}")
        return 1

    report = run_preflight(
        project_root=project_root,
        inventory=inventory,
        upload_env=args.upload_env,
        env_file=env_file,
        apply=args.apply,
        confirm_live_deploy=args.confirm_live_deploy,
    )
    _print_report(report)
    if not report.passed:
        _print_audit(
            action="deploy-preflight",
            outcome="denied",
            subject=inventory.source.name,
            errors=report.errors,
            warnings=report.warnings,
        )
        return 1

    try:
        if args.apply:
            _run_quality_gates(project_root=project_root)
        artifact = build_artifact(
            project_root=project_root,
            output_dir=_resolve_from_project(project_root, args.output_dir),
            require_clean_commit=args.apply,
        )
        print(f"制品={artifact.path}")
        print(f"SHA256={artifact.sha256}")
        if not args.apply:
            _print_audit(
                action="deploy",
                outcome="planned",
                subject=artifact.sha256,
                release_id=artifact.release_id,
                host=inventory.deploy_host,
            )
            print("dry-run 完成：未连接服务器，未执行 Linux 目标操作。")
            return 0
        _deploy_to_linux(
            project_root=project_root,
            inventory=inventory,
            artifact=artifact,
            env_file=env_file,
            args=args,
        )
    except (
        ControlBundleError,
        DeployError,
        PackageError,
        ReleaseManifestError,
        ReleaseSigningError,
    ) as exc:
        _print_audit(action="deploy", outcome="failed", subject=args.inventory.name, reason=str(exc))
        print(f"部署失败：{exc}")
        return 1

    print("部署完成")
    _print_audit(
        action="deploy",
        outcome="success",
        subject=artifact.sha256,
        release_id=artifact.release_id,
        host=inventory.deploy_host,
    )
    print(f"host={inventory.deploy_host}")
    print(f"release={artifact.release_id}")
    print(f"service={inventory.systemd_service_name}.service")
    print(f"mode={inventory.service_mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
