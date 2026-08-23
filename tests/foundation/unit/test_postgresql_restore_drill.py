"""隔离 PostgreSQL 恢复演练的纯边界测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import northstar_quant.foundation.backup.restore_drill as drill
from northstar_quant.foundation.backup.restore_drill import RestoreDrillError


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///northstar_test.db",
        "postgresql+psycopg://northstar@db.example.com:5432/northstar_test",
        "postgresql+psycopg://northstar@127.0.0.1:5432/northstar",
    ],
)
def test_restore_drill_accepts_only_loopback_northstar_test(url: str):
    with pytest.raises(RestoreDrillError):
        drill._test_database_url(url)


def test_restore_drill_client_environment_never_requires_a_password():
    url = drill._test_database_url("postgresql+psycopg://northstar@127.0.0.1:5432/northstar_test")

    environment = drill._client_environment(url)

    assert environment["PGDATABASE"] == "northstar_test"
    assert environment["PGHOST"] == "127.0.0.1"
    assert environment["PGUSER"] == "northstar"
    assert "PGPASSWORD" not in environment


@pytest.mark.parametrize(
    "toc",
    [
        "1; 0 0 DATABASE - northstar_test northstar\n",
        "1; 0 0 TABLE public unrelated northstar\n",
        "1; 0 0 EXTENSION - plpgsql northstar\n",
    ],
)
def test_restore_drill_rejects_toc_outside_generated_schema(toc: str):
    schema_name = "restore_drill_0123456789abcdef0123456789abcdef"

    with pytest.raises(RestoreDrillError, match="TOC"):
        drill._assert_safe_toc(toc, schema_name)


def test_restore_drill_accepts_restricted_schema_toc():
    schema_name = "restore_drill_0123456789abcdef0123456789abcdef"
    toc = "\n".join(
        [
            "; Archive created at 2026-08-22",
            f"1; 0 0 SCHEMA - {schema_name} northstar",
            f"2; 0 0 TABLE {schema_name} sentinel northstar",
            f"3; 0 0 TABLE DATA {schema_name} sentinel northstar",
        ]
    )

    drill._assert_safe_toc(toc, schema_name)


def test_restore_command_restricts_pg_restore_to_the_generated_schema(tmp_path: Path):
    schema_name = "restore_drill_0123456789abcdef0123456789abcdef"

    command = drill._restore_command("pg_restore", schema_name, tmp_path / "drill.dump")

    assert command == [
        "pg_restore",
        "--file=-",
        "--schema",
        schema_name,
        "--no-owner",
        "--no-privileges",
        str(tmp_path / "drill.dump"),
    ]


def test_restore_prefix_recreates_only_the_generated_schema_inside_the_transaction():
    schema_name = "restore_drill_0123456789abcdef0123456789abcdef"

    prefix = drill._restore_prefix(schema_name)

    assert prefix == (
        "BEGIN;\n"
        f'ALTER SCHEMA "{schema_name}" RENAME TO "{schema_name}_source";\n'
        f'CREATE SCHEMA "{schema_name}";\n'
    )


def test_restore_drill_workspace_rejects_symbolic_link(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "workspace"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("当前文件系统不允许创建符号链接。")

    with pytest.raises(RestoreDrillError, match="符号链接"):
        drill._secure_workspace(link)


@pytest.mark.skipif(os.name != "posix", reason="POSIX 权限位只在 Linux/macOS 上可验证")
def test_restore_drill_workspace_rejects_group_or_other_writable_directory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.chmod(0o777)

    with pytest.raises(RestoreDrillError, match="group 或 other 写入"):
        drill._secure_workspace(workspace)
