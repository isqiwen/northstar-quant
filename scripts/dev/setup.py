#!/usr/bin/env python3
"""跨平台开发工作站初始化入口。

开发工具 bootstrap 与项目初始化刻意分开：``--bootstrap-tools`` 默认只展示
可审阅的系统安装计划；执行计划必须同时给出 ``--apply`` 和精确确认。日常项目初始化
由 ``just setup`` 包装依赖同步与安全配置创建；本地 PostgreSQL、迁移和测试仍需要显式入口。

脚本从不下载市场数据、启动调度器、调用 live 交易命令、修改 Docker 用户组，或接受
Docker Desktop 许可。所有开发运行时子进程都被固定到本仓库、本机 PostgreSQL 和
``paper`` 安全模式。
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlparse

try:  # 支持 ``python scripts/dev/setup.py`` 与包内单元测试两种入口。
    from .check_env import PROJECT_ROOT, check_environment
    from .tool_bootstrap import (
        BootstrapPlanError,
        InstallStep,
        build_install_plan,
        execute_install_plan,
        format_command,
    )
except ImportError:  # pragma: no cover - 直接脚本入口会走此分支。
    from check_env import PROJECT_ROOT, check_environment
    from tool_bootstrap import (
        BootstrapPlanError,
        InstallStep,
        build_install_plan,
        execute_install_plan,
        format_command,
    )


ENV_TEMPLATE = PROJECT_ROOT / ".env.example"
ENV_FILE = PROJECT_ROOT / ".env"
APP_TEMPLATE = PROJECT_ROOT / "configs" / "app.example.yaml"
APP_CONFIG = PROJECT_ROOT / "configs" / "app.yaml"
ENV_SYNC_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "sync_env_schema.py"
COMPOSE_FILE = PROJECT_ROOT / "infra" / "docker" / "compose.yaml"
LOCAL_DATABASE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
MINIMUM_PYTHON = (3, 11)
DEVELOPMENT_COMPOSE_PROJECT_NAME = "northstar-quant"
DEVELOPMENT_POSTGRES_VOLUME_NAME = (
    f"{DEVELOPMENT_COMPOSE_PROJECT_NAME}_northstar_postgres_data"
)


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """执行不经 shell 展开的本地工具命令。"""

    print("执行：" + " ".join(command))
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        input=input_text,
        text=True,
        env=dict(env) if env is not None else None,
        check=True,
    )


def _read_env_values(path: Path) -> dict[str, str]:
    """读取活动环境的少量安全门禁字段，绝不输出任何值。"""

    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _read_env_value(key: str) -> str:
    """读取单个本地声明值；调用方绝不把它输出到终端。"""

    return _read_env_values(ENV_FILE).get(key, "")


def _require_regular_local_path(path: Path, label: str) -> None:
    """拒绝通过符号链接把开发初始化定向到仓库外。"""

    if path.is_symlink():
        raise RuntimeError(f"{label} 不能是符号链接；已拒绝写入仓库外目标。")
    if path.exists() and not path.is_file():
        raise RuntimeError(f"{label} 必须是普通文件。")


def _create_file_atomically(path: Path, content: str, *, mode: int) -> None:
    """创建尚不存在的活动文件，避免中断后留下半写入配置。"""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temporary_path, mode)
        if path.exists():
            raise RuntimeError(f"{path} 已在初始化期间被创建；为避免覆盖，已停止。")
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _ensure_existing_app_config_is_usable() -> None:
    """拒绝空、不可读或语法损坏的活动 YAML，绝不静默覆盖它。"""

    try:
        content = APP_CONFIG.read_text(encoding="utf-8")
        if not content.strip():
            raise RuntimeError("configs/app.yaml 为空；请恢复或删除后再显式初始化。")
    except (OSError, UnicodeError) as error:
        raise RuntimeError("无法读取现有 configs/app.yaml；已拒绝覆盖。") from error

    try:
        import yaml
    except ImportError as error:
        raise RuntimeError(
            "无法验证现有 configs/app.yaml；当前 Python 缺少 PyYAML，已拒绝覆盖。"
        ) from error

    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise RuntimeError(
            "现有 configs/app.yaml 的 YAML 格式无效；请恢复或删除后再显式初始化。"
        ) from error
    if not isinstance(payload, Mapping):
        raise RuntimeError(
            "现有 configs/app.yaml 顶层必须是 YAML 对象；请恢复或删除后再显式初始化。"
        )


def _is_true(value: str) -> bool:
    return value.strip().lower() in TRUE_VALUES


def _is_expected_local_database_url(value: str, database_name: str) -> bool:
    """确认现有数据库 URL 是固定的本机开发目标。"""

    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "postgresql+psycopg"
        and parsed.hostname in LOCAL_DATABASE_HOSTS
        and parsed.path.lstrip("/") == database_name
    )


def _unsafe_existing_environment_reasons() -> list[str]:
    """返回不能被普通开发初始化静默重置的活动配置迹象。"""

    values = _read_env_values(ENV_FILE)
    reasons: list[str] = []
    environment = values.get("NORTHSTAR_ENV", "").strip().lower()
    if environment and environment not in {"dev", "test"}:
        reasons.append("NORTHSTAR_ENV 不是 dev/test")
    broker = values.get("NORTHSTAR_BROKER", "").strip().lower()
    if broker and broker != "paper":
        reasons.append("NORTHSTAR_BROKER 不是 paper")
    if _is_true(values.get("NORTHSTAR_LIVE_TRADING_ENABLED", "")):
        reasons.append("NORTHSTAR_LIVE_TRADING_ENABLED=true")
    if _is_true(values.get("NORTHSTAR_KILL_SWITCH_ENABLED", "")):
        reasons.append("NORTHSTAR_KILL_SWITCH_ENABLED=true")

    runtime_url = values.get("NORTHSTAR_DATABASE_URL", "").strip()
    if runtime_url and not _is_expected_local_database_url(runtime_url, "northstar"):
        reasons.append("NORTHSTAR_DATABASE_URL 不是本机 northstar")
    test_url = values.get("NORTHSTAR_TEST_DATABASE_URL", "").strip()
    if test_url and not _is_expected_local_database_url(test_url, "northstar_test"):
        reasons.append("NORTHSTAR_TEST_DATABASE_URL 不是本机 northstar_test")
    if ENV_TEMPLATE.is_file():
        unexpected = sorted(set(values) - set(_read_env_values(ENV_TEMPLATE)))
        if unexpected:
            reasons.append(".env 含未声明字段：" + ", ".join(unexpected))
    return reasons


def _guard_local_development_configuration(*, allow_reset: bool) -> None:
    """阻止普通开发入口覆盖共享、生产或带 kill switch 的活动文件。"""

    _require_regular_local_path(ENV_FILE, ".env")
    _require_regular_local_path(APP_CONFIG, "configs/app.yaml")
    if not ENV_FILE.exists():
        return
    reasons = _unsafe_existing_environment_reasons()
    if not reasons:
        return
    if not allow_reset:
        raise RuntimeError(
            "现有 .env 疑似不是可重置的本地 paper 开发配置（"
            + "；".join(reasons)
            + "）。为防止清除安全门禁，已拒绝写入；如确认这是本地开发文件，"
            "请显式传入 --confirm-reset-local-dev-config YES。"
        )
    print("已确认重置本地开发配置；不会输出原配置值。")


def _initialize_active_configuration(*, allow_reset: bool) -> None:
    """显式创建或规范化本地活动配置，绝不打印敏感值。"""

    if not ENV_TEMPLATE.is_file() or not APP_TEMPLATE.is_file():
        raise RuntimeError("缺少 .env.example 或 configs/app.example.yaml。")
    _guard_local_development_configuration(allow_reset=allow_reset)
    # 在改写 .env 前先确认已存在的活动配置可用。这样中断残留或手工损坏的
    # app.yaml 不会导致配置迁移只完成一半。
    app_config_existed = APP_CONFIG.exists()
    if app_config_existed:
        _ensure_existing_app_config_is_usable()
    _run(
        [
            sys.executable,
            str(ENV_SYNC_SCRIPT),
            "--template",
            str(ENV_TEMPLATE),
            "--active",
            str(ENV_FILE),
            "--apply",
        ]
    )
    if not app_config_existed:
        _create_file_atomically(
            APP_CONFIG,
            APP_TEMPLATE.read_text(encoding="utf-8"),
            mode=0o600,
        )
        print("已创建本地活动配置 configs/app.yaml。")


def _parse_local_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError("POSTGRES_PORT 必须是 1-65535 的数字端口。") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("POSTGRES_PORT 必须是 1-65535 的数字端口。")
    return port


def _local_database_urls(password: str, port: int) -> tuple[str, str]:
    """构造仅指向 loopback 的 URL，并对密码进行 URL 编码。"""

    escaped_password = quote(password, safe="")
    return (
        f"postgresql+psycopg://northstar:{escaped_password}@127.0.0.1:{port}/northstar",
        f"postgresql+psycopg://northstar:{escaped_password}@127.0.0.1:{port}/northstar_test",
    )


def _set_safe_development_values() -> tuple[str, int]:
    """为可选本地 PostgreSQL 写入 paper 开发配置，密码不回显。"""

    password = _read_env_value("POSTGRES_PASSWORD") or secrets.token_hex(18)
    port = _parse_local_port(_read_env_value("POSTGRES_PORT") or "5432")
    runtime_url, test_url = _local_database_urls(password, port)
    updates = {
        "POSTGRES_PASSWORD": password,
        "POSTGRES_PORT": str(port),
        "NORTHSTAR_DATABASE_URL": runtime_url,
        "NORTHSTAR_TEST_DATABASE_URL": test_url,
        "NORTHSTAR_ENV": "dev",
        "NORTHSTAR_BROKER": "paper",
        "NORTHSTAR_LIVE_TRADING_ENABLED": "false",
        "NORTHSTAR_KILL_SWITCH_ENABLED": "false",
    }
    current_values = _read_env_values(ENV_FILE)
    if all(current_values.get(key) == value for key, value in updates.items()):
        print("本地 paper 开发设置已是目标状态；未改写 .env。")
        return password, port
    _run(
        [sys.executable, str(ENV_SYNC_SCRIPT), "--active", str(ENV_FILE), "--set-stdin"],
        input_text="".join(f"{key}={value}\n" for key, value in updates.items()),
    )
    print("已写入本地 paper 开发设置；密码不会输出。")
    return password, port


def _safe_development_environment(password: str, port: int) -> dict[str, str]:
    """为迁移、测试和 Compose 命令去除继承的外部运行时目标。"""

    runtime_url, test_url = _local_database_urls(password, port)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("NORTHSTAR_", "COMPOSE_"))
        and key
        not in {"DOCKER_HOST", "DOCKER_CONTEXT", "POSTGRES_PASSWORD", "POSTGRES_PORT"}
    }
    environment.update(
        {
            "NORTHSTAR_PROJECT_ROOT": str(PROJECT_ROOT),
            "NORTHSTAR_DATABASE_URL": runtime_url,
            "NORTHSTAR_TEST_DATABASE_URL": test_url,
            "NORTHSTAR_ENV": "dev",
            "NORTHSTAR_BROKER": "paper",
            "NORTHSTAR_LIVE_TRADING_ENABLED": "false",
            "NORTHSTAR_KILL_SWITCH_ENABLED": "false",
            "POSTGRES_PASSWORD": password,
            "POSTGRES_PORT": str(port),
            "COMPOSE_PROJECT_NAME": DEVELOPMENT_COMPOSE_PROJECT_NAME,
        }
    )
    return environment


def _assert_local_docker_target(*, environment: Mapping[str, str]) -> None:
    """用实际传给 Compose 的环境拒绝远程 Docker target。"""

    if environment.get("DOCKER_HOST"):
        raise RuntimeError("检测到 DOCKER_HOST；开发 PostgreSQL 只允许本机 Docker daemon。")
    if environment.get("DOCKER_CONTEXT"):
        raise RuntimeError("安全开发环境不能包含 DOCKER_CONTEXT；已拒绝启动本地 PostgreSQL。")
    if not shutil.which("docker"):
        return

    def docker_context_endpoint(context_name: str) -> str:
        if context_name.startswith("-"):
            raise RuntimeError("Docker context 名称无效；已拒绝启动本地 PostgreSQL。")
        endpoint = subprocess.run(
            [
                "docker",
                "context",
                "inspect",
                context_name,
                "--format",
                "{{ .Endpoints.docker.Host }}",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            env=dict(environment),
        )
        docker_host = endpoint.stdout.strip()
        if endpoint.returncode != 0 or not docker_host:
            raise RuntimeError("无法确认 Docker context；已拒绝启动本地 PostgreSQL。")
        return docker_host

    def require_local_context(context_name: str, *, source: str) -> None:
        docker_host = docker_context_endpoint(context_name)
        if not docker_host.startswith(("unix://", "npipe:")):
            raise RuntimeError(
                f"{source} 不是本机 Unix socket 或 Windows named pipe，已拒绝。"
            )

    ambient_context = os.environ.get("DOCKER_CONTEXT", "").strip()
    if ambient_context:
        # Compose 会忽略继承的 DOCKER_CONTEXT；仍需用完全相同的安全环境检查该
        # 显式目标，避免用户以为开发初始化会使用远程 context。
        require_local_context(ambient_context, source="环境变量 DOCKER_CONTEXT")

    context = subprocess.run(
        ["docker", "context", "show"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        env=dict(environment),
    )
    if context.returncode != 0 or not context.stdout.strip():
        raise RuntimeError("无法确认 Docker context；已拒绝启动本地 PostgreSQL。")
    require_local_context(context.stdout.strip(), source="当前 Docker context")


def _compose_command(*arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        DEVELOPMENT_COMPOSE_PROJECT_NAME,
        "--project-directory",
        str(PROJECT_ROOT),
        "-f",
        str(COMPOSE_FILE),
        *arguments,
    ]


def _start_postgres(*, environment: Mapping[str, str]) -> None:
    _assert_local_docker_target(environment=environment)
    _run(_compose_command("up", "-d", "postgres"), env=environment)
    for _ in range(30):
        result = subprocess.run(
            _compose_command(
                "exec", "-T", "postgres", "pg_isready", "-U", "northstar", "-d", "postgres"
            ),
            cwd=PROJECT_ROOT,
            env=dict(environment),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            _ensure_postgresql_database("northstar", environment=environment)
            _ensure_postgresql_database("northstar_test", environment=environment)
            print("本地 PostgreSQL 已就绪。")
            return
        time.sleep(2)
    raise RuntimeError("本地 PostgreSQL 未在 60 秒内就绪。")


def _local_postgres_volume_exists(*, environment: Mapping[str, str]) -> bool:
    """只读检查固定 Compose 项目下的开发卷，不创建或删除任何资源。"""

    result = subprocess.run(
        ["docker", "volume", "inspect", DEVELOPMENT_POSTGRES_VOLUME_NAME],
        cwd=PROJECT_ROOT,
        env=dict(environment),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1 and "no such volume" in result.stderr.lower():
        return False
    raise RuntimeError("无法确认本地 PostgreSQL 数据卷状态，已拒绝继续。")


def _guard_existing_postgres_volume_without_password(
    *, password: str, environment: Mapping[str, str]
) -> None:
    """避免旧卷与新随机密码组合后产生不可恢复的伪初始化。"""

    if password:
        return
    _assert_local_docker_target(environment=environment)
    if _local_postgres_volume_exists(environment=environment):
        raise RuntimeError(
            "检测到已有本地 PostgreSQL 数据卷，但 POSTGRES_PASSWORD 为空；"
            "为避免用新密码启动旧数据，已拒绝继续。请恢复原密码，或按独立、明确的"
            "本地数据重置流程处理；本脚本绝不会删除数据卷。"
        )


def _postgresql_database_exists(name: str, *, environment: Mapping[str, str]) -> bool:
    """在固定本地 Compose 项目中只读确认数据库是否存在。"""

    result = subprocess.run(
        _compose_command(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "northstar",
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname='{name}'",
        ),
        cwd=PROJECT_ROOT,
        env=dict(environment),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == "1"


def _ensure_postgresql_database(name: str, *, environment: Mapping[str, str]) -> None:
    """确保旧 Docker 卷也拥有隔离测试数据库，并收敛并发创建竞争。"""

    if _postgresql_database_exists(name, environment=environment):
        return
    try:
        _run(
            _compose_command("exec", "-T", "postgres", "createdb", "-U", "northstar", name),
            env=environment,
        )
    except subprocess.CalledProcessError:
        # 两个开发初始化进程可能同时看到数据库缺失。若另一个进程已经成功创建，
        # 第二个 createdb 的重复错误不应把幂等初始化变成失败。
        if _postgresql_database_exists(name, environment=environment):
            print(f"本地数据库 {name} 已由并发初始化创建；继续。")
            return
        raise


def _missing_bootstrap_tools(*, include_docker: bool) -> set[str]:
    """列出缺失的工具；Docker 状态检查不会连接 daemon。"""

    missing = {tool for tool in ("uv", "just", "git") if not shutil.which(tool)}
    if not include_docker:
        return missing
    if not shutil.which("docker"):
        return missing | {"docker", "docker-compose-v2"}
    compose = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if compose.returncode != 0:
        missing.add("docker-compose-v2")
    return missing


def _bootstrap_python_error(version: tuple[int, int] | None = None) -> str | None:
    """确保安装器自身也不会掩盖不受支持的 Python 运行时。"""

    current = version or sys.version_info[:2]
    if current >= MINIMUM_PYTHON:
        return None
    return (
        "开发工作站需要 Python "
        f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} 或更高版本；"
        "请先按操作系统的受支持方式安装 Python，再运行工具 bootstrap。"
    )


def _render_install_plan(steps: list[InstallStep]) -> None:
    print("开发工具安装计划（默认只展示，不会执行）：")
    for number, step in enumerate(steps, 1):
        print(f"{number}. {step.label}")
        print(f"   {format_command(step.command)}")
        if step.note:
            print(f"   注意：{step.note}")


def _bootstrap_tools(args: argparse.Namespace) -> int:
    """显示或在双重确认后执行系统工具安装计划。"""

    python_error = _bootstrap_python_error()
    if python_error:
        print(python_error, file=sys.stderr)
        return 1
    missing = _missing_bootstrap_tools(include_docker=args.install_docker)
    try:
        steps = build_install_plan(
            missing_tools=missing,
            install_docker=args.install_docker,
        )
    except BootstrapPlanError as error:
        print(f"无法生成开发工具安装计划：{error}", file=sys.stderr)
        return 1
    if not steps:
        print("所选开发工具均已可用；未执行任何系统安装。")
        return 0

    _render_install_plan(steps)
    if not args.apply:
        print("如已审阅计划，请重新运行并附加 --apply --confirm-tool-install YES。")
        return 0
    if args.confirm_tool_install != "YES":
        print("执行系统工具安装必须显式传入 --confirm-tool-install YES。", file=sys.stderr)
        return 2
    if args.install_docker and args.confirm_docker_install != "YES":
        print("执行 Docker 安装还必须显式传入 --confirm-docker-install YES。", file=sys.stderr)
        return 2

    try:
        execute_install_plan(steps)
    except subprocess.CalledProcessError as error:
        print(f"工具安装失败（退出码 {error.returncode}）；未继续项目初始化。", file=sys.stderr)
        return error.returncode or 1
    except OSError as error:
        print(f"无法启动工具安装命令：{error}", file=sys.stderr)
        return 1
    print("工具安装命令已完成。请按安装器提示重启终端或 Docker Desktop，然后重新运行开发初始化。")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化 Northstar Quant 跨平台开发工作站。")
    parser.add_argument(
        "--bootstrap-tools",
        action="store_true",
        help="展示缺失的 uv、just、Git 安装计划；默认不执行。",
    )
    parser.add_argument(
        "--install-docker",
        action="store_true",
        help="与 --bootstrap-tools 一起将 Docker Desktop/Engine + Compose v2 纳入计划。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行已展示的工具安装计划；必须同时给出精确确认。",
    )
    parser.add_argument(
        "--confirm-tool-install",
        default="",
        metavar="YES",
        help="仅接受 YES，确认执行系统级开发工具安装。",
    )
    parser.add_argument(
        "--confirm-docker-install",
        default="",
        metavar="YES",
        help="仅接受 YES，确认 Docker Desktop/Engine 安装计划。",
    )
    parser.add_argument(
        "--initialize-config",
        action="store_true",
        help="显式创建或迁移未跟踪的 .env 与 configs/app.yaml。",
    )
    parser.add_argument(
        "--confirm-reset-local-dev-config",
        default="",
        metavar="YES",
        help="仅接受 YES，允许将疑似非开发 .env 重置为本地 paper 配置。",
    )
    parser.add_argument(
        "--with-postgres",
        action="store_true",
        help="启动本机 Docker PostgreSQL，并将活动环境固定为 paper 开发模式。",
    )
    parser.add_argument("--migrate", action="store_true", help="在本地 PostgreSQL 就绪后运行迁移。")
    parser.add_argument("--run-tests", action="store_true", help="运行跨平台单元测试与 Ruff。")
    parser.add_argument("--check-only", action="store_true", help="仅检查，不写入或启动任何本地服务。")
    return parser.parse_args()


def _bootstrap_args_are_valid(args: argparse.Namespace) -> str | None:
    """保持工具安装与项目配置、数据库操作两个权限边界相互独立。"""

    if args.install_docker and not args.bootstrap_tools:
        return "--install-docker 必须与 --bootstrap-tools 同时使用。"
    if args.apply and not args.bootstrap_tools:
        return "--apply 仅可与 --bootstrap-tools 一起使用。"
    if (args.confirm_tool_install or args.confirm_docker_install) and not args.bootstrap_tools:
        return "工具安装确认参数必须与 --bootstrap-tools 一起使用。"
    if not args.bootstrap_tools:
        return None
    if any(
        (
            args.initialize_config,
            args.with_postgres,
            args.migrate,
            args.run_tests,
            args.check_only,
            args.confirm_reset_local_dev_config,
        )
    ):
        return "--bootstrap-tools 不能与项目配置、数据库、测试或依赖同步选项混用。"
    return None


def main() -> int:
    args = _parse_args()
    bootstrap_error = _bootstrap_args_are_valid(args)
    if bootstrap_error:
        print(bootstrap_error, file=sys.stderr)
        return 2
    if args.bootstrap_tools:
        return _bootstrap_tools(args)

    if args.check_only and any(
        (args.initialize_config, args.with_postgres, args.migrate, args.run_tests)
    ):
        print("--check-only 不能与写入、迁移或测试选项同时使用。", file=sys.stderr)
        return 2
    if args.migrate and not args.with_postgres:
        print("--migrate 必须与 --with-postgres 同时使用。", file=sys.stderr)
        return 2
    if args.with_postgres and not args.initialize_config:
        print("--with-postgres 必须与 --initialize-config 同时使用。", file=sys.stderr)
        return 2
    if args.confirm_reset_local_dev_config and not args.initialize_config:
        print("--confirm-reset-local-dev-config 必须与 --initialize-config 同时使用。", file=sys.stderr)
        return 2

    checks = check_environment(
        require_config=not args.initialize_config,
        require_docker=args.with_postgres,
        require_just=False,
        require_git=False,
    )
    errors = [item for item in checks if item["status"] == "error"]
    for item in checks:
        print(f"[{item['status']}] {item['name']}：{item['message']}")
    if errors:
        return 1
    if args.check_only:
        print("开发环境检查通过；未写入任何文件或启动服务。")
        return 0

    if args.initialize_config:
        _initialize_active_configuration(
            allow_reset=args.confirm_reset_local_dev_config == "YES"
        )

    existing_password = _read_env_value("POSTGRES_PASSWORD")
    port = _parse_local_port(_read_env_value("POSTGRES_PORT") or "5432")
    pre_initialization_environment = _safe_development_environment(existing_password, port)
    if args.with_postgres:
        try:
            _guard_existing_postgres_volume_without_password(
                password=existing_password,
                environment=pre_initialization_environment,
            )
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            print(f"本地 PostgreSQL 初始化预检失败：{error}", file=sys.stderr)
            return 1

    if args.initialize_config:
        password, port = _set_safe_development_values()
    else:
        password = existing_password or secrets.token_hex(18)
    safe_environment = _safe_development_environment(password, port)

    if args.with_postgres:
        _start_postgres(environment=safe_environment)
    if args.migrate:
        _run(["uv", "run", "--offline", "--no-sync", "northstar", "init-db"], env=safe_environment)
    if args.run_tests:
        print("执行：领域 unit 测试")
        subprocess.run(
            [
                "uv",
                "run",
                "--offline",
                "--no-sync",
                "pytest",
                "tests/data/unit",
                "tests/intelligence/unit",
                "tests/research/unit",
                "tests/portfolio_risk/unit",
                "tests/trading_execution/unit",
                "tests/foundation/unit",
                "-q",
            ],
            cwd=PROJECT_ROOT,
            env=safe_environment,
            check=True,
        )
        _run(["uv", "run", "--offline", "--no-sync", "ruff", "check", "."], env=safe_environment)
    print("跨平台开发工作站初始化完成；未执行数据下载、调度或交易操作。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
