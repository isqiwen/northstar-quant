#!/usr/bin/env python3
"""Linux x86_64 部署前检查。

此模块不连接服务器、不执行 Shell，也不会输出环境文件的值。真正执行时，Linux
后端仍会重新运行同等（且更严格）的门禁，避免客户端检查被绕过。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

try:  # 允许作为 ``python scripts/deploy/preflight.py`` 或模块导入运行。
    from .inventory import DeploymentInventory, InventoryError, load_inventory
    from .platform_support import PlatformSupportError, require_linux_x86_64
except ImportError:  # pragma: no cover - 直接脚本执行路径。
    from inventory import DeploymentInventory, InventoryError, load_inventory
    from platform_support import PlatformSupportError, require_linux_x86_64


class PreflightError(ValueError):
    """活动环境文件或本地发布状态不安全。"""


def _require_linux_x86_64_host() -> None:
    """Reject unsupported controllers before local preflight work begins."""

    try:
        require_linux_x86_64()
    except PlatformSupportError as exc:
        raise PreflightError(str(exc)) from exc


@dataclass
class PreflightReport:
    """部署前检查的可读结果，不包含任何机密内容。"""

    checks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors

    def require_success(self) -> None:
        if self.errors:
            raise PreflightError("；".join(self.errors))


def _read_environment(path: Path) -> dict[str, str]:
    """读取 ``.env`` 的字面值，不执行变量替换或命令替换。"""

    if not path.is_file():
        raise PreflightError(f"未找到活动环境文件：{path}")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PreflightError(f"活动环境文件第 {line_number} 行不是 KEY=VALUE。")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key or any(character.isspace() for character in key):
            raise PreflightError(f"活动环境文件第 {line_number} 行字段名无效。")
        if key in values:
            raise PreflightError(f"活动环境文件重复定义字段：{key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if "\x00" in value:
            raise PreflightError(f"活动环境文件第 {line_number} 行包含空字节。")
        values[key] = value
    return values


def validate_production_environment(
    env_file: Path,
    template_file: Path,
    *,
    service_mode: str,
    confirm_live_deploy: str,
) -> None:
    """在上传前按 Linux 后端相同的交易安全规则校验活动 ``.env``。"""

    active = _read_environment(env_file)
    template = _read_environment(template_file)
    if active.keys() != template.keys():
        missing = sorted(template.keys() - active.keys())
        extra = sorted(active.keys() - template.keys())
        details: list[str] = []
        if missing:
            details.append(f"缺少 {len(missing)} 个字段")
        if extra:
            details.append(f"多出 {len(extra)} 个字段")
        raise PreflightError(f"活动 .env 与 .env.example 字段结构不一致（{'，'.join(details)}）。")

    if active.get("NORTHSTAR_ENV") != "production":
        raise PreflightError("服务器环境必须设置 NORTHSTAR_ENV=production。")
    database_url = active.get("NORTHSTAR_DATABASE_URL", "")
    if not database_url.startswith("postgresql+psycopg://"):
        raise PreflightError("NORTHSTAR_DATABASE_URL 必须是 PostgreSQL psycopg URL。")
    if "CHANGE_ME" in database_url or "本地密码" in database_url:
        raise PreflightError("NORTHSTAR_DATABASE_URL 仍包含示例占位符。")

    broker = active.get("NORTHSTAR_BROKER", "paper").lower()
    live_enabled = active.get("NORTHSTAR_LIVE_TRADING_ENABLED", "false").lower()
    if service_mode != "scheduler":
        if broker != "paper" or live_enabled != "false":
            raise PreflightError("health 模式要求 broker=paper 且 live trading=false。")
        return
    if broker == "paper":
        if live_enabled != "false":
            raise PreflightError("paper 调度器要求 NORTHSTAR_LIVE_TRADING_ENABLED=false。")
        return
    if broker != "ctp" or live_enabled != "true":
        raise PreflightError("非 paper 调度器要求 broker=ctp 且 live trading=true。")
    if confirm_live_deploy != "YES":
        raise PreflightError("检测到真实交易调度器，必须明确设置 --confirm-live-deploy YES。")


def _git_worktree_is_clean(project_root: Path) -> bool | None:
    """返回 Git 工作区状态；非 Git 环境返回 ``None``。"""

    if shutil.which("git") is None:
        return None
    result = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain", "--untracked-files=normal"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return not result.stdout.strip()


def run_preflight(
    *,
    project_root: Path,
    inventory: DeploymentInventory,
    upload_env: bool,
    env_file: Path | None,
    apply: bool,
    confirm_live_deploy: str,
) -> PreflightReport:
    """执行纯本地检查；不会建立 SSH 连接或改变目标服务器。"""

    _require_linux_x86_64_host()
    report = PreflightReport()
    required_paths = (
        project_root / "pyproject.toml",
        project_root / "uv.lock",
        project_root / "configs" / "app.example.yaml",
        project_root / "scripts" / "deploy" / "deploy.py",
        project_root / "scripts" / "deploy" / "package.py",
        project_root / "scripts" / "deploy" / "control_bundle.py",
        project_root / "scripts" / "deploy" / "release_manifest.py",
        project_root / "scripts" / "deploy" / "release_signing.py",
        project_root / "scripts" / "deploy" / "release_transaction.py",
        project_root / "scripts" / "deploy" / "release_transaction_hook.py",
        project_root / "scripts" / "deploy" / "root_release_runner.py",
        project_root / "scripts" / "deploy" / "gate_release.sh",
        project_root / "scripts" / "deploy" / "install-runtime.sh",
        project_root / "scripts" / "deploy" / "install-release.sh",
        project_root / "scripts" / "deploy" / "release_gate_bootstrap.py",
        project_root / "infra" / "systemd" / "health.service.in",
        project_root / "infra" / "systemd" / "scheduler.service.in",
    )
    missing = [str(path.relative_to(project_root)) for path in required_paths if not path.exists()]
    if missing:
        report.errors.append(f"部署所需文件缺失：{', '.join(missing)}")
    else:
        report.checks.append("部署文件清单完整")

    clean = _git_worktree_is_clean(project_root)
    if clean is None:
        report.warnings.append("无法读取 Git 工作区状态；Linux 后端会在执行时重新检查。")
    elif clean:
        report.checks.append("Git 工作区干净")
    else:
        report.errors.append("工作区存在未提交修改；部署制品必须来自已提交的可复现 revision。")

    if inventory.service_mode == "health":
        report.checks.append("服务模式为 health，默认不会启动交易调度器")
    else:
        report.warnings.append("服务模式为 scheduler；远端仍会按 broker 与 live 开关失败关闭。")

    if upload_env:
        if env_file is None:
            report.errors.append("--upload-env 需要指定 --env-file。")
        else:
            try:
                validate_production_environment(
                    env_file,
                    project_root / ".env.example",
                    service_mode=inventory.service_mode,
                    confirm_live_deploy=confirm_live_deploy,
                )
            except PreflightError as exc:
                report.errors.append(str(exc))
            else:
                report.checks.append("待上传活动 .env 通过生产与交易安全检查")
    else:
        report.checks.append("未请求上传活动 .env")

    if apply:
        for command in ("ssh", "ssh-keygen", "uv"):
            if shutil.which(command) is None:
                report.errors.append(f"--apply 需要本机命令：{command}")
        if not report.errors:
            report.checks.append("Linux 后端所需本机命令可用")
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行 Northstar Quant Linux x86_64 部署前检查。")
    parser.add_argument("--inventory", type=Path, required=True, help="非机密部署清单路径。")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="项目根目录。")
    parser.add_argument("--upload-env", action="store_true", help="校验待上传的活动 .env。")
    parser.add_argument("--env-file", type=Path, help="唯一活动环境文件，文件名必须为 .env。")
    parser.add_argument("--apply", action="store_true", help="检查实际部署所需的本机工具。")
    parser.add_argument(
        "--confirm-live-deploy",
        choices=("NO", "YES"),
        default="NO",
        help="仅在真实交易 scheduler 已经过人工确认时使用 YES。",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    project_root = args.project_root.resolve()
    inventory_path = (
        args.inventory.resolve()
        if args.inventory.is_absolute()
        else (project_root / args.inventory).resolve()
    )
    if args.env_file is not None and not args.upload_env:
        print("部署前检查失败：--env-file 必须与 --upload-env 一起使用。")
        return 2
    env_file: Path | None = None
    if args.upload_env:
        requested_env_file = args.env_file or Path(".env")
        env_file = (
            requested_env_file.resolve()
            if requested_env_file.is_absolute()
            else (project_root / requested_env_file).resolve()
        )
    if env_file is not None and env_file.name != ".env":
        print("部署前检查失败：活动环境文件必须命名为 .env。")
        return 2
    try:
        inventory = load_inventory(inventory_path)
    except InventoryError as exc:
        print(f"部署前检查失败：{exc}")
        return 1
    report = run_preflight(
        project_root=project_root,
        inventory=inventory,
        upload_env=args.upload_env,
        env_file=env_file,
        apply=args.apply,
        confirm_live_deploy=args.confirm_live_deploy,
    )
    for check in report.checks:
        print(f"通过：{check}")
    for warning in report.warnings:
        print(f"警告：{warning}")
    for error in report.errors:
        print(f"失败：{error}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
