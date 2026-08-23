"""PostgreSQL 备份客户端边界测试。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

import northstar_quant.platform.backup.postgresql as postgresql
from northstar_quant.platform.backup.postgresql import PostgreSQLBackupError


_URL = "postgresql+psycopg://backup_user:s%40fe-password@127.0.0.1:5432/northstar?sslmode=require"  # secret-scan: allow; reason: disposable test fixture


def _install_successful_client(monkeypatch, calls: list[tuple[list[str], dict[str, str]]]) -> None:
    monkeypatch.setattr(
        postgresql.shutil,
        "which",
        lambda executable, **kwargs: f"/usr/bin/{executable}",
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        if "--file" in command:
            Path(command[command.index("--file") + 1]).write_bytes(b"PGDMP\x01")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(postgresql.subprocess, "run", fake_run)


def test_custom_dump_uses_private_environment_and_verifies_archive(tmp_path: Path, monkeypatch):
    calls: list[tuple[list[str], dict[str, str]]] = []
    _install_successful_client(monkeypatch, calls)
    output = tmp_path / "database.dump"

    result = postgresql.create_postgresql_dump(_URL, output_path=output, timeout_seconds=30)

    assert result.path == output.resolve()
    assert result.size_bytes == len(b"PGDMP\x01")
    assert [call[0][0] for call in calls] == ["/usr/bin/pg_dump", "/usr/bin/pg_restore"]
    dump_command, dump_environment = calls[0]
    assert dump_command[1:] == [
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--quote-all-identifiers",
        "--file",
        str(output.resolve()),
    ]
    assert "s@fe-password" not in " ".join(dump_command)
    assert dump_environment["PGDATABASE"] == "northstar"
    assert dump_environment["PGHOST"] == "127.0.0.1"
    assert dump_environment["PGPASSWORD"] == "s@fe-password"
    assert dump_environment["PGSSLMODE"] == "require"
    assert "PGPASSWORD" not in calls[1][1]


def test_dump_rejects_existing_output_without_invoking_client(tmp_path: Path, monkeypatch):
    output = tmp_path / "database.dump"
    output.write_bytes(b"existing")
    monkeypatch.setattr(
        postgresql.shutil,
        "which",
        lambda executable, **kwargs: f"/usr/bin/{executable}",
    )

    with pytest.raises(PostgreSQLBackupError, match="已存在"):
        postgresql.create_postgresql_dump(_URL, output_path=output)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits require Linux validation")
def test_dump_rejects_group_or_other_writable_staging_parent(tmp_path: Path):
    output_parent = tmp_path / "unsafe-staging"
    output_parent.mkdir()
    output_parent.chmod(0o777)

    with pytest.raises(PostgreSQLBackupError, match="group 或 other 写入"):
        postgresql._reserve_new_output(output_parent / "database.dump")


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///not-allowed.db",
        "postgresql+psycopg://backup_user:safe-password@127.0.0.1:5432/northstar?options=-csearch_path%3Dhidden",  # secret-scan: allow; reason: disposable test fixture
        "postgresql+psycopg://backup_user@127.0.0.1:5432/northstar",
    ],
)
def test_dump_rejects_unsafe_database_configuration(database_url: str, tmp_path: Path):
    with pytest.raises(PostgreSQLBackupError):
        postgresql.create_postgresql_dump(database_url, output_path=tmp_path / "database.dump")


def test_client_failure_is_redacted(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        postgresql.shutil,
        "which",
        lambda executable, **kwargs: f"/usr/bin/{executable}",
    )
    monkeypatch.setattr(
        postgresql.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1),
    )

    with pytest.raises(PostgreSQLBackupError) as raised:
        postgresql.create_postgresql_dump(_URL, output_path=tmp_path / "database.dump")

    assert "s@fe-password" not in str(raised.value)
    assert "127.0.0.1" not in str(raised.value)


def test_verify_rejects_empty_or_symbolic_archive(tmp_path: Path):
    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")

    with pytest.raises(PostgreSQLBackupError, match="非空普通文件"):
        postgresql.verify_postgresql_dump(empty)

    outside = tmp_path / "outside.dump"
    outside.write_bytes(b"PGDMP")
    link = tmp_path / "link.dump"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前文件系统不允许创建符号链接。")

    with pytest.raises(PostgreSQLBackupError, match="符号链接"):
        postgresql.verify_postgresql_dump(link)
