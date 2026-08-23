"""受限 PostgreSQL 自定义格式逻辑转储辅助函数。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Final

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


_DEFAULT_TIMEOUT_SECONDS: Final = 60 * 60
_SAFE_POSIX_PATH: Final = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_LIBPQ_QUERY_ENV: Final = {
    "application_name": "PGAPPNAME",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


class PostgreSQLBackupError(RuntimeError):
    """逻辑转储或静态归档验证失败，且不暴露数据库凭据。"""


@dataclass(frozen=True, slots=True)
class PostgreSQLDump:
    """已生成并通过格式检查的自定义格式转储摘要。"""

    path: Path
    size_bytes: int


def create_postgresql_dump(
    database_url: str,
    *,
    output_path: str | Path,
    executable: str = "pg_dump",
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> PostgreSQLDump:
    """以 ``pg_dump`` 自定义格式创建整个 PostgreSQL 数据库的逻辑转储。

    连接信息仅经受限子进程环境传递；命令行不含 DSN、用户或密码，失败异常也不
    拼接客户端输出。调用方必须提供一个尚不存在且位于私有 staging 目录的路径。
    """

    environment = _database_environment(database_url)
    target = _reserve_new_output(Path(output_path))
    command = [
        _require_executable(executable),
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--quote-all-identifiers",
        "--file",
        str(target),
    ]
    _run_client(
        command,
        environment=environment,
        timeout_seconds=timeout_seconds,
        label="PostgreSQL 转储",
    )
    _validate_nonempty_regular_file(target, "PostgreSQL 转储")
    verify_postgresql_dump(
        target,
        timeout_seconds=timeout_seconds,
    )
    return PostgreSQLDump(path=target, size_bytes=target.stat().st_size)


def verify_postgresql_dump(
    archive_path: str | Path,
    *,
    executable: str = "pg_restore",
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """使用 ``pg_restore --list`` 验证自定义格式档案，不连接任何数据库。"""

    archive = _validate_nonempty_regular_file(Path(archive_path), "PostgreSQL 转储")
    command = [_require_executable(executable), "--list", str(archive)]
    _run_client(
        command,
        environment=_safe_client_environment(),
        timeout_seconds=timeout_seconds,
        label="PostgreSQL 转储格式检查",
    )


def _database_environment(database_url: str) -> dict[str, str]:
    try:
        url = make_url(database_url.strip())
    except (ArgumentError, ValueError) as exc:
        raise PostgreSQLBackupError("数据库连接配置不是有效 PostgreSQL URL。") from exc
    if url.drivername != "postgresql+psycopg":
        raise PostgreSQLBackupError("备份仅支持 postgresql+psycopg 数据库 URL。")
    if not url.host or not url.database or not url.username:
        raise PostgreSQLBackupError("备份数据库 URL 必须显式包含 host、database 和 user。")
    if url.password is None:
        raise PostgreSQLBackupError("备份数据库 URL 必须通过受控秘密提供 password。")
    environment = _safe_client_environment()
    environment.update(
        {
            "PGDATABASE": url.database,
            "PGHOST": url.host,
            "PGPASSWORD": str(url.password),
            "PGPORT": str(url.port or 5432),
            "PGUSER": url.username,
        }
    )
    _apply_supported_libpq_query_options(url, environment)
    return environment


def _apply_supported_libpq_query_options(url: URL, environment: dict[str, str]) -> None:
    for key, raw_value in url.query.items():
        env_name = _LIBPQ_QUERY_ENV.get(key)
        if env_name is None or not isinstance(raw_value, str) or not raw_value:
            raise PostgreSQLBackupError("备份数据库 URL 包含未允许的连接参数。")
        environment[env_name] = raw_value


def _safe_client_environment() -> dict[str, str]:
    path = os.environ.get("PATH", "") if os.name == "nt" else _SAFE_POSIX_PATH
    if not path:
        raise PostgreSQLBackupError("无法建立受限 PostgreSQL 客户端环境。")
    return {"LC_ALL": "C", "PATH": path}


def _reserve_new_output(path: Path) -> Path:
    if path.is_symlink() or path.exists():
        raise PostgreSQLBackupError("PostgreSQL 转储输出已存在；拒绝覆盖。")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise PostgreSQLBackupError("PostgreSQL 转储输出父目录不安全。")
    parent_mode = parent.lstat().st_mode
    if os.name == "posix" and parent_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PostgreSQLBackupError("PostgreSQL 转储输出父目录不能允许 group 或 other 写入。")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise PostgreSQLBackupError("无法预留 PostgreSQL 转储输出文件。") from exc
    else:
        os.close(descriptor)
    return path.resolve(strict=True)


def _validate_nonempty_regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise PostgreSQLBackupError(f"{label}不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
        mode = path.lstat().st_mode
        size = path.stat().st_size
    except OSError as exc:
        raise PostgreSQLBackupError(f"{label}不存在或无法读取。") from exc
    if not stat.S_ISREG(mode) or size < 1:
        raise PostgreSQLBackupError(f"{label}必须是非空普通文件。")
    return resolved


def _require_executable(executable: str) -> str:
    found = shutil.which(executable, path=None if os.name == "nt" else _SAFE_POSIX_PATH)
    if found is None:
        raise PostgreSQLBackupError(f"未找到必需 PostgreSQL 客户端：{executable}。")
    return found


def _run_client(
    command: list[str],
    *,
    environment: dict[str, str],
    timeout_seconds: int,
    label: str,
) -> None:
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise PostgreSQLBackupError("PostgreSQL 客户端超时必须是正整数。")
    try:
        completed = subprocess.run(
            command,
            check=False,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except OSError as exc:
        raise PostgreSQLBackupError(f"无法启动{label}客户端。") from exc
    except subprocess.TimeoutExpired as exc:
        raise PostgreSQLBackupError(f"{label}超时。") from exc
    if completed.returncode != 0:
        raise PostgreSQLBackupError(f"{label}失败；客户端输出已抑制以防泄露敏感信息。")
