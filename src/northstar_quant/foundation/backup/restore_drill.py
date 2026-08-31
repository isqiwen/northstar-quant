"""仅限隔离 ``northstar_test`` 数据库的 PostgreSQL 逻辑恢复演练。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Final
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from northstar_quant.foundation.platform_support import require_linux_x86_64


_TEST_DATABASE_NAME: Final = "northstar_test"
_LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
_SCHEMA_PATTERN: Final = re.compile(r"^restore_drill_[0-9a-f]{32}$")
_SAFE_LINUX_PATH: Final = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_TIMEOUT_SECONDS: Final = 120
_DATA_MARKER: Final = "NORTHSTAR_RESTORE_DRILL_DATA_OK"
_SCHEMA_MARKER: Final = "NORTHSTAR_RESTORE_DRILL_SCHEMA_OK"


class RestoreDrillError(RuntimeError):
    """隔离恢复演练没有通过受限安全契约。"""


@dataclass(frozen=True, slots=True)
class RestoreDrillResult:
    """成功演练的无秘密摘要；归档和 source schema 都被保留供审计。"""

    schema_name: str
    archive_path: Path
    archive_sha256: str


@dataclass(frozen=True, slots=True)
class _SourceSchemaIdentity:
    schema_oid: int
    sentinel_table_oid: int


def run_test_postgresql_restore_drill(
    database_url: str,
    *,
    workspace_dir: str | Path,
    timeout_seconds: int = _TIMEOUT_SECONDS,
) -> RestoreDrillResult:
    """对临时 schema 执行真实 ``pg_dump``/``pg_restore`` 事务回滚演练。

    只接受本机 ``northstar_test``。演练 source schema 和生成的 archive 都不会自动
    清理；恢复阶段仅在单个事务中临时改名 schema，并始终以 ``ROLLBACK`` 收尾。
    """

    require_linux_x86_64()
    url = _test_database_url(database_url)
    workspace = _secure_workspace(Path(workspace_dir))
    _validate_timeout(timeout_seconds)
    executables = _required_clients()
    schema_name = f"restore_drill_{uuid4().hex}"
    archive_path = _reserve_archive(workspace / f"{schema_name}.dump")
    environment = _client_environment(url)
    engine = create_engine(url, future=True)
    source_identity: _SourceSchemaIdentity | None = None
    try:
        source_identity = _create_source_schema(engine, schema_name)
        _run_dump(
            executable=executables["pg_dump"],
            schema_name=schema_name,
            archive_path=archive_path,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        _validate_archive_toc(
            executable=executables["pg_restore"],
            schema_name=schema_name,
            archive_path=archive_path,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        _restore_inside_rollback(
            psql=executables["psql"],
            pg_restore=executables["pg_restore"],
            schema_name=schema_name,
            archive_path=archive_path,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        _assert_source_schema_preserved(engine, schema_name, source_identity)
    finally:
        engine.dispose()
    return RestoreDrillResult(
        schema_name=schema_name,
        archive_path=archive_path,
        archive_sha256=_sha256(archive_path),
    )


def _test_database_url(database_url: str) -> URL:
    try:
        url = make_url(database_url.strip())
    except (ArgumentError, ValueError) as exc:
        raise RestoreDrillError("NORTHSTAR_TEST_DATABASE_URL 不是有效 PostgreSQL URL。") from exc
    if url.drivername != "postgresql+psycopg":
        raise RestoreDrillError("恢复演练只支持 postgresql+psycopg。")
    if url.host not in _LOOPBACK_HOSTS:
        raise RestoreDrillError("恢复演练只允许本机 loopback PostgreSQL。")
    if url.database != _TEST_DATABASE_NAME:
        raise RestoreDrillError("恢复演练只允许精确的 northstar_test 数据库。")
    if not url.username:
        raise RestoreDrillError("恢复演练数据库 URL 必须包含 user。")
    return url


def _secure_workspace(path: Path) -> Path:
    if path.is_symlink():
        raise RestoreDrillError("恢复演练工作目录不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
        mode = path.lstat().st_mode
    except OSError as exc:
        raise RestoreDrillError("恢复演练工作目录不存在或无法安全读取。") from exc
    if not stat.S_ISDIR(mode):
        raise RestoreDrillError("恢复演练工作目录必须是目录。")
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RestoreDrillError("恢复演练工作目录不能允许 group 或 other 写入。")
    return resolved


def _required_clients() -> dict[str, str]:
    clients = {
        name: shutil.which(name, path=_SAFE_LINUX_PATH)
        for name in ("pg_dump", "pg_restore", "psql")
    }
    missing = sorted(name for name, path in clients.items() if path is None)
    if missing:
        raise RestoreDrillError("缺少 PostgreSQL 恢复演练客户端：" + ", ".join(missing))
    return {name: path for name, path in clients.items() if path is not None}


def _reserve_archive(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        raise RestoreDrillError("恢复演练 archive 已存在；拒绝覆盖。")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise RestoreDrillError("无法预留恢复演练 archive。") from exc
    else:
        os.close(descriptor)
    return path.resolve(strict=True)


def _client_environment(url: URL) -> dict[str, str]:
    environment = {
        "LC_ALL": "C",
        "PATH": _SAFE_LINUX_PATH,
        "PGDATABASE": _TEST_DATABASE_NAME,
        "PGHOST": str(url.host),
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
    }
    if url.password is not None:
        environment["PGPASSWORD"] = str(url.password)
    return environment


def _create_source_schema(engine, schema_name: str) -> _SourceSchemaIdentity:
    _validate_schema_name(schema_name)
    quoted_schema = _quote_identifier(schema_name)
    with engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        connection.execute(
            text(
                f"CREATE TABLE {quoted_schema}.sentinel "
                "(id integer PRIMARY KEY, marker text NOT NULL)"
            )
        )
        connection.execute(
            text(f"INSERT INTO {quoted_schema}.sentinel (id, marker) VALUES (1, 'northstar')")
        )
        schema_oid = connection.scalar(
            text("SELECT oid FROM pg_namespace WHERE nspname = :schema_name"),
            {"schema_name": schema_name},
        )
        table_oid = connection.scalar(
            text(
                "SELECT pg_class.oid FROM pg_class "
                "JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
                "WHERE pg_namespace.nspname = :schema_name AND pg_class.relname = 'sentinel'"
            ),
            {"schema_name": schema_name},
        )
    if type(schema_oid) is not int or type(table_oid) is not int:
        raise RestoreDrillError("无法记录恢复演练 source schema 身份。")
    return _SourceSchemaIdentity(schema_oid=schema_oid, sentinel_table_oid=table_oid)


def _run_dump(
    *,
    executable: str,
    schema_name: str,
    archive_path: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> None:
    _run_silent(
        [
            executable,
            "--format=custom",
            "--schema",
            schema_name,
            "--no-owner",
            "--no-privileges",
            "--file",
            str(archive_path),
        ],
        environment=environment,
        timeout_seconds=timeout_seconds,
        label="pg_dump",
    )
    _require_nonempty_archive(archive_path)


def _validate_archive_toc(
    *,
    executable: str,
    schema_name: str,
    archive_path: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> None:
    try:
        result = subprocess.run(
            [executable, "--list", str(archive_path)],
            check=False,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_seconds,
        )
    except OSError as exc:
        raise RestoreDrillError("无法启动 pg_restore。") from exc
    except subprocess.TimeoutExpired as exc:
        raise RestoreDrillError("pg_restore TOC 检查超时。") from exc
    if result.returncode != 0:
        raise RestoreDrillError("pg_restore TOC 检查失败；客户端输出已抑制。")
    _assert_safe_toc(result.stdout, schema_name)


def _assert_safe_toc(toc: str, schema_name: str) -> None:
    relevant_lines = [line for line in toc.splitlines() if line and not line.startswith(";")]
    if not relevant_lines:
        raise RestoreDrillError("恢复演练 archive 没有可验证对象。")
    forbidden = (" DATABASE ", " ACL ", " DEFAULT ACL ", " EXTENSION ", " FUNCTION ")
    for line in relevant_lines:
        normalized = f" {line.upper()} "
        if any(token in normalized for token in forbidden) or schema_name not in line:
            raise RestoreDrillError("恢复演练 archive TOC 包含超出受控 schema 的对象。")


def _restore_inside_rollback(
    *,
    psql: str,
    pg_restore: str,
    schema_name: str,
    archive_path: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> None:
    prefix = _restore_prefix(schema_name)
    suffix = (
        "\n"
        "SELECT CASE WHEN count(*) = 1 THEN '"
        + _DATA_MARKER
        + "' ELSE 'NORTHSTAR_RESTORE_DRILL_DATA_MISSING' END "
        f"FROM {_quote_identifier(schema_name)}.sentinel "
        "WHERE id = 1 AND marker = 'northstar';\n"
        "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '"
        + schema_name
        + "') THEN '"
        + _SCHEMA_MARKER
        + "' ELSE 'NORTHSTAR_RESTORE_DRILL_SCHEMA_MISSING' END;\n"
        "ROLLBACK;\n"
    )
    try:
        psql_process = subprocess.Popen(
            [
                psql,
                "--no-psqlrc",
                "--set",
                "ON_ERROR_STOP=1",
                "--tuples-only",
                "--no-align",
                "--quiet",
            ],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as exc:
        raise RestoreDrillError("无法启动 psql。") from exc
    try:
        assert psql_process.stdin is not None
        psql_process.stdin.write(prefix)
        psql_process.stdin.flush()
        restore_process = subprocess.Popen(
            _restore_command(pg_restore, schema_name, archive_path),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=psql_process.stdin,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            restore_process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            restore_process.kill()
            restore_process.wait()
            raise RestoreDrillError("pg_restore SQL 生成超时。") from exc
        if restore_process.returncode != 0:
            raise RestoreDrillError("pg_restore SQL 生成失败；客户端输出已抑制。")
        psql_process.stdin.write(suffix)
        psql_process.stdin.close()
        try:
            output = (psql_process.stdout.read() if psql_process.stdout is not None else "")
            psql_process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            psql_process.kill()
            psql_process.wait()
            raise RestoreDrillError("psql 恢复事务超时。") from exc
        if psql_process.returncode != 0:
            raise RestoreDrillError("psql 恢复事务失败；客户端输出已抑制。")
    finally:
        if psql_process.poll() is None:
            psql_process.kill()
            psql_process.wait()
    if _DATA_MARKER not in output or _SCHEMA_MARKER not in output:
        raise RestoreDrillError("恢复演练没有验证原 schema 的数据和对象。")


def _restore_command(pg_restore: str, schema_name: str, archive_path: Path) -> list[str]:
    _validate_schema_name(schema_name)
    return [
        pg_restore,
        "--file=-",
        "--schema",
        schema_name,
        "--no-owner",
        "--no-privileges",
        str(archive_path),
    ]


def _restore_prefix(schema_name: str) -> str:
    _validate_schema_name(schema_name)
    source_schema = f"{schema_name}_source"
    return (
        "BEGIN;\n"
        f"ALTER SCHEMA {_quote_identifier(schema_name)} RENAME TO {_quote_identifier(source_schema)};\n"
        f"CREATE SCHEMA {_quote_identifier(schema_name)};\n"
    )


def _assert_source_schema_preserved(
    engine,
    schema_name: str,
    source_identity: _SourceSchemaIdentity,
) -> None:
    source_schema = f"{schema_name}_source"
    with engine.connect() as connection:
        actual_schema_oid = connection.scalar(
            text("SELECT oid FROM pg_namespace WHERE nspname = :schema_name"),
            {"schema_name": schema_name},
        )
        transient_oid = connection.scalar(
            text("SELECT oid FROM pg_namespace WHERE nspname = :schema_name"),
            {"schema_name": source_schema},
        )
        actual_table_oid = connection.scalar(
            text(
                "SELECT pg_class.oid FROM pg_class "
                "JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
                "WHERE pg_namespace.nspname = :schema_name AND pg_class.relname = 'sentinel'"
            ),
            {"schema_name": schema_name},
        )
        row_count = connection.scalar(
            text(f"SELECT count(*) FROM {_quote_identifier(schema_name)}.sentinel")
        )
        marker = connection.scalar(
            text(
                f"SELECT marker FROM {_quote_identifier(schema_name)}.sentinel "
                "WHERE id = 1"
            )
        )
    if (
        actual_schema_oid != source_identity.schema_oid
        or actual_table_oid != source_identity.sentinel_table_oid
        or transient_oid is not None
        or row_count != 1
        or marker != "northstar"
    ):
        raise RestoreDrillError("恢复演练事务未保持 source schema 不变。")


def _run_silent(
    command: list[str],
    *,
    environment: dict[str, str],
    timeout_seconds: int,
    label: str,
) -> None:
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
        raise RestoreDrillError(f"无法启动 {label}。") from exc
    except subprocess.TimeoutExpired as exc:
        raise RestoreDrillError(f"{label} 超时。") from exc
    if completed.returncode != 0:
        raise RestoreDrillError(f"{label} 失败；客户端输出已抑制。")


def _require_nonempty_archive(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        raise RestoreDrillError("恢复演练 archive 不是非空普通文件。")


def _validate_schema_name(schema_name: str) -> None:
    if not _SCHEMA_PATTERN.fullmatch(schema_name):
        raise RestoreDrillError("恢复演练 schema 名不符合受控格式。")


def _quote_identifier(value: str) -> str:
    _validate_schema_name(value.removesuffix("_source"))
    return f'"{value}"'


def _validate_timeout(timeout_seconds: int) -> None:
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise RestoreDrillError("恢复演练超时必须是正整数。")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
