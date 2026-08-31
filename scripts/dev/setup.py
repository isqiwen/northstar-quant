#!/usr/bin/env python3
"""Linux x86_64 开发工作站初始化入口。

``--initialize-workstation`` 是首次开发环境的统一入口：缺少仓库本地 ``uv``、``just`` 或宿主机 Git 时，
它会先展示可审阅的工具安装计划，并在交互终端要求精确确认；安装后会重新检查工具，
能够在当前进程定位时立即完成依赖同步、安全配置、本机 PostgreSQL 的默认安装/启动及前向迁移。
只有刚安装的宿主机工具尚未被当前进程定位时，才要求重新打开终端并重新运行入口。``--bootstrap-tools``
保留为只预览或显式执行工具安装的低层入口；测试和分步诊断仍使用各自显式入口。

高层入口在 Ubuntu/Debian 上仅可安装发行版 PostgreSQL 包并启用默认服务；它不会停止、重置或删除服务、角色、数据库、
schema 或数据目录。所有开发运行时子进程都被固定到本仓库、loopback PostgreSQL 和 ``paper`` 安全模式。已有角色、
凭据或数据库状态未知时，初始化会在迁移前失败关闭。
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlparse

try:  # 支持 ``python scripts/dev/setup.py`` 与包内单元测试两种入口。
    from .check_env import PROJECT_ROOT, check_environment
    from .project_tools import (
        ProjectToolError,
        repository_just_executable,
        repository_tool_root,
        repository_uv_executable,
    )
    from .platform_support import PlatformSupportError, require_linux_x86_64
    from .tool_bootstrap import (
        BootstrapPlanError,
        InstallStep,
        build_native_postgresql_plan,
        build_install_plan,
        execute_install_plan,
        format_command,
    )
except ImportError:  # pragma: no cover - 直接脚本入口会走此分支。
    from check_env import PROJECT_ROOT, check_environment
    from project_tools import (
        ProjectToolError,
        repository_just_executable,
        repository_tool_root,
        repository_uv_executable,
    )
    from platform_support import PlatformSupportError, require_linux_x86_64
    from tool_bootstrap import (
        BootstrapPlanError,
        InstallStep,
        build_native_postgresql_plan,
        build_install_plan,
        execute_install_plan,
        format_command,
    )


ENV_TEMPLATE = PROJECT_ROOT / ".env.example"
ENV_FILE = PROJECT_ROOT / ".env"
APP_TEMPLATE = PROJECT_ROOT / "configs" / "app.example.yaml"
APP_CONFIG = PROJECT_ROOT / "configs" / "app.yaml"
ENV_SYNC_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "sync_env_schema.py"
LOCAL_DATABASE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
LOCAL_DATABASE_HOST = "127.0.0.1"
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
MINIMUM_PYTHON = (3, 11)
DEFAULT_NATIVE_POSTGRES_PORT = 5432
NATIVE_POSTGRESQL_CLIENT_COMMANDS = (
    "pg_isready",
    "psql",
    "createdb",
    "pg_dump",
    "pg_restore",
)
NORTHSTAR_POSTGRES_ROLE = "northstar"
NATIVE_POSTGRES_SERVICE_WAIT_ATTEMPTS = 20
NATIVE_POSTGRES_SERVICE_WAIT_SECONDS = 0.25

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
    """为已配置的 native PostgreSQL 写入 paper 开发配置，密码不回显。"""

    password = _read_env_value("POSTGRES_PASSWORD")
    if not password or password == "CHANGE_ME":
        raise RuntimeError(
            "POSTGRES_PASSWORD 必须先在 .env 中设置为已配置的本机 northstar PostgreSQL 角色密码；"
            "初始化不会生成、修改或输出数据库凭据。"
        )
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
    """为迁移、测试和原生 PostgreSQL 客户端去除继承的外部运行时目标。"""

    runtime_url, test_url = _local_database_urls(password, port)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("NORTHSTAR_")
        and not key.startswith("PG")
        and key
        not in {
            "POSTGRES_PASSWORD",
            "POSTGRES_PORT",
        }
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
            "PGDATABASE": "postgres",
            "PGHOST": LOCAL_DATABASE_HOST,
            "PGHOSTADDR": LOCAL_DATABASE_HOST,
            "PGPASSWORD": password,
            "PGPORT": str(port),
            "PGUSER": "northstar",
        }
    )
    return environment


def _missing_native_postgresql_client_tools() -> set[str]:
    """返回完整本机 PostgreSQL 工作流缺失的客户端命令。"""

    return {
        command
        for command in NATIVE_POSTGRESQL_CLIENT_COMMANDS
        if shutil.which(command) is None
    }


def _native_postgresql_service_is_ready(*, port: int) -> bool:
    """只读确认固定 loopback PostgreSQL 服务是否接受连接。"""

    if shutil.which("pg_isready") is None:
        return False
    try:
        readiness = subprocess.run(
            [
                "pg_isready",
                "--host",
                LOCAL_DATABASE_HOST,
                "--port",
                str(port),
                "--dbname",
                "postgres",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
            env=_postgres_administrator_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return readiness.returncode == 0


def _wait_for_native_postgresql_service(*, port: int) -> bool:
    """等待刚启动的默认服务短暂就绪，超时后保持失败关闭。"""

    for attempt in range(NATIVE_POSTGRES_SERVICE_WAIT_ATTEMPTS):
        if _native_postgresql_service_is_ready(port=port):
            return True
        if attempt + 1 < NATIVE_POSTGRES_SERVICE_WAIT_ATTEMPTS:
            time.sleep(NATIVE_POSTGRES_SERVICE_WAIT_SECONDS)
    return False


def _render_native_postgresql_plan(steps: list[InstallStep]) -> None:
    """展示高层入口将默认执行的受限 PostgreSQL 系统步骤。"""

    print("本机 PostgreSQL 默认安装计划：")
    for number, step in enumerate(steps, 1):
        print(f"{number}. {step.label}")
        print(f"   {format_command(step.command)}")
        if step.note:
            print(f"   注意：{step.note}")


def _ensure_native_postgresql_for_workstation(*, port: int) -> None:
    """为高层 Linux 初始化安装/启动默认服务后重新验证本机前提。"""

    missing_tools = _missing_native_postgresql_client_tools()
    service_ready = _native_postgresql_service_is_ready(port=port)
    if not missing_tools and service_ready:
        print("本机 PostgreSQL 客户端与 loopback 服务均已可用；未执行系统安装。")
        return

    if port != DEFAULT_NATIVE_POSTGRES_PORT:
        raise RuntimeError(
            "默认 PostgreSQL 安装仅管理 5432 端口；当前 .env 配置了其他端口且服务未就绪，"
            "不会修改 PostgreSQL 服务配置。"
        )
    try:
        plan = build_native_postgresql_plan(install_packages=bool(missing_tools))
    except BootstrapPlanError as error:
        raise RuntimeError(f"无法生成本机 PostgreSQL 默认安装计划：{error}") from error
    if shutil.which("sudo") is None:
        raise RuntimeError("缺少 sudo，无法安全执行 Ubuntu/Debian 本机 PostgreSQL 默认安装。")
    if shutil.which("systemctl") is None:
        raise RuntimeError("缺少 systemctl，无法安全管理本机 PostgreSQL 默认服务。")
    _render_native_postgresql_plan(plan)
    try:
        execute_install_plan(plan)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"本机 PostgreSQL 默认安装或启动失败（退出码 {error.returncode}）；未继续初始化。"
        ) from error
    except OSError as error:
        raise RuntimeError(f"无法启动本机 PostgreSQL 安装命令：{error}") from error

    missing_after_install = _missing_native_postgresql_client_tools()
    if missing_after_install:
        names = "、".join(sorted(missing_after_install))
        raise RuntimeError(f"PostgreSQL 安装后仍缺少客户端命令：{names}。")
    if not _wait_for_native_postgresql_service(port=port):
        raise RuntimeError(
            f"默认 PostgreSQL 服务未能在 {LOCAL_DATABASE_HOST}:{port} 就绪；"
            "不会修改服务配置、认证规则或数据目录。"
        )
    print("本机 PostgreSQL 默认服务已就绪。")


def _quote_postgresql_literal(value: str, *, label: str) -> str:
    """生成只供 stdin SQL 使用的 PostgreSQL text literal，拒绝多行或 NUL 输入。"""

    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise RuntimeError(f"{label} 不能为空且不能包含换行或 NUL 字符。")
    return "'" + value.replace("'", "''") + "'"


def _postgres_administrator_psql_command(*arguments: str) -> list[str]:
    """构造仅通过本机 postgres OS 身份运行的无交互 psql 命令。"""

    return [
        "sudo",
        "-n",
        "-u",
        "postgres",
        "psql",
        "--no-psqlrc",
        "--dbname",
        "postgres",
        "--set",
        "ON_ERROR_STOP=1",
        *arguments,
    ]


def _postgres_administrator_environment() -> dict[str, str]:
    """移除继承的 PostgreSQL/runtime 变量，保持管理员 psql 为本机默认 socket。"""

    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PG")
        and not key.startswith("NORTHSTAR_")
        and key not in {"POSTGRES_PASSWORD", "POSTGRES_PORT"}
    }


def _ensure_postgres_administrator_access() -> None:
    """在发送 SQL stdin 前取得 sudo 认证，避免密码提示吞掉 SQL。"""

    if shutil.which("sudo") is None:
        raise RuntimeError("缺少 sudo，无法安全检查或创建本机 northstar PostgreSQL 角色。")
    try:
        _run(["sudo", "-v"])
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            "无法获得本机 PostgreSQL 管理员权限；不会修改既有角色、密码或数据库。"
        ) from error


def _run_postgres_administrator_sql(sql: str, *, tuples_only: bool) -> subprocess.CompletedProcess[str]:
    """经 stdin 执行受限本机管理员 SQL，绝不把 SQL 或密码置入 argv/日志。"""

    arguments: list[str] = []
    if tuples_only:
        arguments.extend(("--tuples-only", "--no-align"))
    return subprocess.run(
        _postgres_administrator_psql_command(*arguments),
        cwd=PROJECT_ROOT,
        input=sql,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=_postgres_administrator_environment(),
    )


def _northstar_postgresql_role_exists() -> bool:
    """用 postgres catalog 只读确认固定开发角色是否已存在。"""

    result = _run_postgres_administrator_sql(
        f"SELECT 1 FROM pg_roles WHERE rolname = '{NORTHSTAR_POSTGRES_ROLE}';\n",
        tuples_only=True,
    )
    if result.returncode != 0:
        raise RuntimeError("无法查询本机 PostgreSQL 角色目录；已拒绝继续初始化。")
    return result.stdout.strip() == "1"


def _create_northstar_postgresql_role(password: str) -> None:
    """仅创建缺失的最小本机开发角色；绝不 ALTER 或删除现有角色。"""

    password_literal = _quote_postgresql_literal(password, label="POSTGRES_PASSWORD")
    sql = "\n".join(
        (
            "SELECT pg_advisory_lock(418843248117);",
            f"CREATE ROLE {NORTHSTAR_POSTGRES_ROLE} LOGIN CREATEDB NOSUPERUSER NOCREATEROLE "
            f"NOREPLICATION NOBYPASSRLS PASSWORD {password_literal};",
            "SELECT pg_advisory_unlock(418843248117);",
            "",
        )
    )
    result = _run_postgres_administrator_sql(sql, tuples_only=False)
    if result.returncode != 0:
        raise RuntimeError(
            "无法创建缺失的本机 northstar PostgreSQL 角色；不会修改任何既有角色或密码。"
        )
    print("已确认本机 northstar PostgreSQL 角色存在；既有角色不会被修改。")


def _write_generated_local_postgres_password(password: str) -> None:
    """将仅用于新角色的随机密码原子写入受保护的本地 .env。"""

    _run(
        [sys.executable, str(ENV_SYNC_SCRIPT), "--active", str(ENV_FILE), "--set-stdin"],
        input_text=f"POSTGRES_PASSWORD={password}\n",
    )
    print("已将新建本机 northstar 角色的密码写入 .env；密码不会输出。")


def _ensure_northstar_postgresql_role() -> str:
    """复用已验证角色，或只在角色不存在时安全创建它。"""

    configured_password = _read_env_value("POSTGRES_PASSWORD")
    has_configured_password = bool(configured_password) and configured_password != "CHANGE_ME"
    if has_configured_password:
        port = _parse_local_port(
            _read_env_value("POSTGRES_PORT") or str(DEFAULT_NATIVE_POSTGRES_PORT)
        )
        try:
            _assert_native_postgres_ready(
                environment=_safe_development_environment(configured_password, port),
                port=port,
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            # 普通角色认证失败时只查询角色是否存在；绝不猜测或覆盖既有密码。
            _ensure_postgres_administrator_access()
            if _northstar_postgresql_role_exists():
                raise RuntimeError(
                    "现有 northstar PostgreSQL 角色的密码或 loopback 访问规则无法验证；"
                    "不会修改既有角色或密码，请在 .env 中提供匹配凭据后重试。"
                )
            _create_northstar_postgresql_role(configured_password)
            return configured_password
        return configured_password

    _ensure_postgres_administrator_access()
    if _northstar_postgresql_role_exists():
        raise RuntimeError(
            "现有 northstar PostgreSQL 角色未提供可验证的 POSTGRES_PASSWORD；"
            "不会生成覆盖它的密码，请在 .env 中填写该现有角色的密码后重试。"
        )
    generated_password = secrets.token_hex(32)
    _create_northstar_postgresql_role(generated_password)
    _write_generated_local_postgres_password(generated_password)
    return generated_password


def _native_postgres_command(command: str, *, port: int, arguments: tuple[str, ...]) -> list[str]:
    """构造固定到 loopback 本机服务的 PostgreSQL 客户端命令。"""

    return [
        command,
        "--host",
        LOCAL_DATABASE_HOST,
        "--port",
        str(port),
        "--username",
        NORTHSTAR_POSTGRES_ROLE,
        *arguments,
    ]


def _assert_native_postgres_ready(*, environment: Mapping[str, str], port: int) -> None:
    """验证已运行的本机服务及 .env 中的角色凭据，不管理服务生命周期。"""

    readiness = subprocess.run(
        [
            "pg_isready",
            "--host",
            LOCAL_DATABASE_HOST,
            "--port",
            str(port),
            "--dbname",
            "postgres",
        ],
        cwd=PROJECT_ROOT,
        env=dict(environment),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=5,
    )
    if readiness.returncode != 0:
        raise RuntimeError(
            f"本机 PostgreSQL 未在 {LOCAL_DATABASE_HOST}:{port} 就绪；"
            "请由操作者启动原生 PostgreSQL 服务后重试。"
        )
    probe = subprocess.run(
        _native_postgres_command(
            "psql",
            port=port,
            arguments=("--no-psqlrc", "--dbname", "postgres", "--tuples-only", "--no-align", "--command", "SELECT 1"),
        ),
        cwd=PROJECT_ROOT,
        env=dict(environment),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "1":
        raise RuntimeError(
            "无法使用 .env 中的 northstar 本机 PostgreSQL 凭据连接 postgres 数据库；"
            "请由操作者创建或修复该本地角色、密码和访问规则后重试。"
        )


def _postgresql_database_exists(name: str, *, environment: Mapping[str, str], port: int) -> bool:
    """通过 native psql 只读确认数据库是否存在。"""

    if name not in {"northstar", "northstar_test"}:
        raise RuntimeError("只允许检查 Northstar 本地开发数据库。")
    result = subprocess.run(
        _native_postgres_command(
            "psql",
            port=port,
            arguments=(
                "--no-psqlrc",
                "--dbname",
                "postgres",
                "--tuples-only",
                "--no-align",
                "--command",
                f"SELECT 1 FROM pg_database WHERE datname = '{name}'",
            ),
        ),
        cwd=PROJECT_ROOT,
        env=dict(environment),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError("无法查询本机 PostgreSQL 数据库目录；已拒绝继续初始化。")
    return result.stdout.strip() == "1"


def _ensure_postgresql_database(name: str, *, environment: Mapping[str, str], port: int) -> None:
    """仅创建缺失的本地开发数据库；绝不清空或替换既有数据库。"""

    if _postgresql_database_exists(name, environment=environment, port=port):
        return
    try:
        _run(
            _native_postgres_command(
                "createdb",
                port=port,
                arguments=("--maintenance-db", "postgres", name),
            ),
            env=environment,
        )
    except subprocess.CalledProcessError:
        # 两个开发初始化进程可能同时看到数据库缺失。若另一个进程已经成功创建，
        # 第二个 createdb 的重复错误不应把幂等初始化变成失败。
        if _postgresql_database_exists(name, environment=environment, port=port):
            print(f"本地数据库 {name} 已由并发初始化创建；继续。")
            return
        raise


def _prepare_native_postgres(*, environment: Mapping[str, str], port: int) -> None:
    """复用已运行的 native PostgreSQL，并确保两个隔离开发数据库存在。"""

    _assert_native_postgres_ready(environment=environment, port=port)
    _ensure_postgresql_database("northstar", environment=environment, port=port)
    _ensure_postgresql_database("northstar_test", environment=environment, port=port)
    print("本机 PostgreSQL 已验证并可复用。")


def _missing_bootstrap_tools() -> set[str]:
    """列出缺失的仓库本地工具与宿主机 Git。"""

    missing = {"git"} if not shutil.which("git") else set()
    try:
        repository_uv_executable()
    except ProjectToolError:
        missing.add("uv")
    try:
        repository_just_executable()
    except ProjectToolError:
        missing.add("just")
    return missing


def _require_owned_writable_directory(
    path: Path,
    *,
    label: str,
    allow_missing: bool = False,
) -> None:
    """验证用户级 bootstrap 目录，不接管其他所有者的文件。"""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise RuntimeError(f"{label} 不存在。") from None
    except OSError as error:
        raise RuntimeError(f"无法检查 {label}：{error}") from error

    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"{label}不能是符号链接。")
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"{label}必须是目录。")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and metadata.st_uid != getuid():
        raise RuntimeError(f"{label}不属于当前用户；不会修改它。")
    if not os.access(path, os.W_OK | os.X_OK):
        raise RuntimeError(f"当前用户没有 {label} 的写入权限。")


def _repository_tool_root_for_bootstrap() -> Path:
    """验证将由确认后的工具 bootstrap 创建的仓库本地目录。"""

    _require_owned_writable_directory(PROJECT_ROOT, label="仓库根目录")
    tool_root = repository_tool_root()
    _require_owned_writable_directory(
        tool_root,
        label="仓库 .northstar 工具目录",
        allow_missing=True,
    )
    for relative_path in (
        "bootstrap",
        "bootstrap/pipx",
        "cache",
        "cache/pip",
        "downloads",
        "pipx",
        "bin",
        "man",
        "state",
    ):
        _require_owned_writable_directory(
            tool_root / relative_path,
            label=f"仓库 .northstar/{relative_path} 目录",
            allow_missing=True,
        )
    return tool_root


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


def _render_install_plan(
    steps: list[InstallStep],
    *,
    tool_root: Path | None = None,
) -> None:
    print("开发工具安装计划：")
    for number, step in enumerate(steps, 1):
        print(f"{number}. {step.label}")
        print(f"   {format_command(step.command)}")
        if step.note:
            print(f"   注意：{step.note}")
    if tool_root is not None:
        print(f"   仓库本地工具目录：{tool_root}")
        print("   注意：工具仅写入仓库 .northstar；项目命令按路径调用 uv 和 just，无需修改 PATH。")


def _interactive_bootstrap_confirmations(
    *,
    tool_confirmation: str,
) -> str:
    """在首次初始化的交互终端中取得缺失工具的显式确认。"""

    if tool_confirmation:
        return tool_confirmation
    if not sys.stdin.isatty():
        sys.stdout.flush()
        print(
            "当前终端无法交互确认开发工具安装；请在交互终端重新运行开发初始化，"
            "或显式传入 --confirm-tool-install YES。",
            file=sys.stderr,
        )
        return tool_confirmation
    try:
        tool_confirmation = input("输入 YES 以确认安装以上开发工具：").strip()
    except EOFError:
        print("未收到开发工具安装确认；已停止开发初始化。", file=sys.stderr)
    return tool_confirmation


def _bootstrap_tools_with_result(
    args: argparse.Namespace,
    *,
    prompt_for_confirmation: bool = False,
) -> tuple[int, bool]:
    """显示或执行工具安装计划，并返回是否实际执行了安装命令。"""

    python_error = _bootstrap_python_error()
    if python_error:
        print(python_error, file=sys.stderr)
        return 1, False
    missing = _missing_bootstrap_tools()
    tool_root: Path | None = None
    if {"uv", "just"} & missing:
        try:
            tool_root = _repository_tool_root_for_bootstrap()
        except RuntimeError as error:
            print(f"无法为仓库本地工具准备目录：{error}", file=sys.stderr)
            return 1, False
    try:
        steps = build_install_plan(
            missing_tools=missing,
            project_tool_root=tool_root,
            python_executable=sys.executable,
        )
    except BootstrapPlanError as error:
        print(f"无法生成开发工具安装计划：{error}", file=sys.stderr)
        return 1, False
    if not steps:
        print("所选开发工具均已可用；未执行任何安装命令。")
        return 0, False

    _render_install_plan(steps, tool_root=tool_root)
    if not args.apply:
        print("这是预览；未执行任何安装命令。如已审阅计划，请重新运行并附加 --apply --confirm-tool-install YES。")
        return 0, False
    tool_confirmation = args.confirm_tool_install
    if prompt_for_confirmation:
        tool_confirmation = _interactive_bootstrap_confirmations(
            tool_confirmation=tool_confirmation,
        )
    if tool_confirmation != "YES":
        print("执行开发工具安装必须显式传入 --confirm-tool-install YES。", file=sys.stderr)
        return 2, False
    try:
        execute_install_plan(steps)
    except subprocess.CalledProcessError as error:
        print(f"工具安装失败（退出码 {error.returncode}）；未继续项目初始化。", file=sys.stderr)
        return error.returncode or 1, False
    except OSError as error:
        print(f"无法启动工具安装命令：{error}", file=sys.stderr)
        return 1, False
    print("工具安装命令已完成。仓库本地 uv 与 just 可立即通过 .northstar/bin 调用。")
    return 0, True


def _bootstrap_tools(args: argparse.Namespace) -> int:
    """显示或在明确确认后执行开发工具安装计划。"""

    result, _ = _bootstrap_tools_with_result(args)
    return result


def _initialize_workstation(args: argparse.Namespace) -> int:
    """先确保工具和原生 PostgreSQL 可用，再执行受控项目初始化。"""

    bootstrap_args = argparse.Namespace(
        apply=True,
        confirm_tool_install=args.confirm_tool_install,
    )
    result, installed_tools = _bootstrap_tools_with_result(
        bootstrap_args,
        prompt_for_confirmation=True,
    )
    if result:
        return result
    if installed_tools:
        remaining = _missing_bootstrap_tools()
        if remaining:
            names = "、".join(sorted(remaining))
            print(
                f"工具安装已完成，但当前进程仍无法定位：{names}。"
                "请重新打开终端后再次运行开发初始化。",
                file=sys.stderr,
            )
            return 2

    try:
        # 必须先拒绝外部、live 或疑似生产配置，才允许高层入口提升权限安装/启动服务。
        _guard_local_development_configuration(allow_reset=False)
        port = _parse_local_port(
            _read_env_value("POSTGRES_PORT") or str(DEFAULT_NATIVE_POSTGRES_PORT)
        )
        _ensure_native_postgresql_for_workstation(port=port)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"本机 PostgreSQL 默认安装预检失败：{error}", file=sys.stderr)
        return 1

    readiness = check_environment(
        require_config=False,
        require_postgres=True,
        require_just=True,
        require_git=True,
    )
    errors = [item for item in readiness if item["status"] == "error"]
    if errors:
        details = "；".join(f"{item['name']}：{item['message']}" for item in errors)
        print(f"本地 PostgreSQL 初始化预检失败：{details}", file=sys.stderr)
        return 1

    try:
        just = str(repository_just_executable())
        # 现有 app.yaml 的语法验证需要项目依赖；先 materialize 受审计环境，再安全创建
        # 活动配置并为新主机 provision 受限角色。随后沿用标准 dev-postgres 配方迁移。
        _run([just, "env-bootstrap"])
        _initialize_active_configuration(allow_reset=False)
        _ensure_northstar_postgresql_role()
        _set_safe_development_values()
        _run([just, "dev-postgres"])
    except subprocess.CalledProcessError as error:
        print(f"开发初始化失败（退出码 {error.returncode}）。", file=sys.stderr)
        return error.returncode or 1
    except (OSError, ProjectToolError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"开发初始化失败：{error}", file=sys.stderr)
        return 1
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化 Northstar Quant Linux x86_64 开发工作站。")
    parser.add_argument(
        "--bootstrap-tools",
        action="store_true",
        help="展示缺失的仓库本地 uv、just 与宿主机 Git 安装计划；默认不执行。",
    )
    parser.add_argument(
        "--initialize-workstation",
        action="store_true",
        help="首次完整入口：Ubuntu/Debian 默认安装/启用本机 PostgreSQL，随后初始化并前向迁移。",
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
        help="仅接受 YES，确认执行开发工具安装。",
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
        help="仅供底层 --initialize-config 流程验证并复用本机 PostgreSQL。",
    )
    parser.add_argument("--migrate", action="store_true", help="在本地 PostgreSQL 就绪后运行迁移。")
    parser.add_argument("--run-tests", action="store_true", help="运行 Linux 单元测试与 Ruff。")
    parser.add_argument("--check-only", action="store_true", help="仅检查，不写入或管理任何本地服务。")
    return parser.parse_args()


def _bootstrap_args_are_valid(args: argparse.Namespace) -> str | None:
    """保持工具安装与项目配置、数据库操作两个权限边界相互独立。"""

    if args.bootstrap_tools and args.initialize_workstation:
        return "--bootstrap-tools 不能与 --initialize-workstation 同时使用。"
    if args.apply and not args.bootstrap_tools:
        return "--apply 仅可与 --bootstrap-tools 一起使用。"
    if args.confirm_tool_install and not (
        args.bootstrap_tools or args.initialize_workstation
    ):
        return "工具安装确认参数必须与 --bootstrap-tools 或 --initialize-workstation 一起使用。"
    if args.initialize_workstation:
        if args.with_postgres:
            return "--initialize-workstation 已包含本地 PostgreSQL 初始化与迁移；请移除 --with-postgres。"
        if any(
            (
                args.initialize_config,
                args.migrate,
                args.run_tests,
                args.check_only,
                args.confirm_reset_local_dev_config,
            )
        ):
            return "--initialize-workstation 不能与底层项目配置、迁移、测试或检查选项混用。"
        return None
    if not args.bootstrap_tools:
        return None
    if any(
        (
            args.initialize_workstation,
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
    try:
        require_linux_x86_64()
    except PlatformSupportError as error:
        print(f"开发初始化失败：{error}", file=sys.stderr)
        return 1
    if args.bootstrap_tools:
        return _bootstrap_tools(args)
    if args.initialize_workstation:
        return _initialize_workstation(args)

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
        require_postgres=args.with_postgres,
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

    if args.initialize_config:
        try:
            password, port = _set_safe_development_values()
        except RuntimeError as error:
            print(f"本地 PostgreSQL 配置预检失败：{error}", file=sys.stderr)
            return 1
    else:
        password = _read_env_value("POSTGRES_PASSWORD")
        port = _parse_local_port(_read_env_value("POSTGRES_PORT") or "5432")
    safe_environment = _safe_development_environment(password, port)

    if args.with_postgres:
        try:
            _prepare_native_postgres(environment=safe_environment, port=port)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            print(f"本地 PostgreSQL 初始化预检失败：{error}", file=sys.stderr)
            return 1
    if args.migrate:
        uv = str(repository_uv_executable())
        _run([uv, "run", "--offline", "--no-sync", "northstar", "init-db"], env=safe_environment)
    if args.run_tests:
        uv = str(repository_uv_executable())
        print("执行：领域 unit 测试")
        subprocess.run(
            [
                uv,
                "run",
                "--offline",
                "--no-sync",
                "pytest",
                "tests/application/unit",
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
        _run([uv, "run", "--offline", "--no-sync", "ruff", "check", "."], env=safe_environment)
    print("Linux 开发工作站初始化完成；未执行数据下载、调度或交易操作。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
