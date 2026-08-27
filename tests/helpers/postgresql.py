"""PostgreSQL 测试 schema 隔离工具。"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.schema import CreateSchema

from tests.helpers.paths import PROJECT_ROOT

_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://northstar@127.0.0.1:5432/northstar_test"
)
_LOCAL_TEST_DATABASE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TEST_DATABASE_NAME = "northstar_test"
_TEST_CONNECTION_PARALLELISM_OPTION = "-cmax_parallel_workers_per_gather=0"
_SCHEMA_BY_KEY: dict[str, str] = {}
_LOCK = Lock()


def _base_test_url() -> URL:
    dotenv_database_url = dotenv_values(PROJECT_ROOT / ".env").get(
        "NORTHSTAR_TEST_DATABASE_URL"
    )
    database_url = (
        os.getenv("NORTHSTAR_TEST_DATABASE_URL")
        or dotenv_database_url
        or _DEFAULT_TEST_DATABASE_URL
    ).strip()
    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg":
        raise RuntimeError(
            "NORTHSTAR_TEST_DATABASE_URL 必须使用 postgresql+psycopg://。"
        )
    if url.host not in _LOCAL_TEST_DATABASE_HOSTS:
        raise RuntimeError(
            "NORTHSTAR_TEST_DATABASE_URL 仅允许连接本机 loopback PostgreSQL。"
        )
    if url.database != _TEST_DATABASE_NAME:
        raise RuntimeError(
            "NORTHSTAR_TEST_DATABASE_URL 必须精确指向 northstar_test，"
            "已拒绝使用运行时或其他数据库。"
        )
    return url


def postgresql_test_url(key: str | Path) -> str:
    """为一个测试键创建并复用独立 PostgreSQL schema。

    测试结束后不会自动删除 schema 或清空测试数据库。需要清理时必须由操作者在
    独立的手工流程中完成，避免测试框架拥有任何自动破坏性数据库权限。
    """

    normalized_key = str(key)
    with _LOCK:
        schema = _SCHEMA_BY_KEY.get(normalized_key)
        if schema is None:
            schema = f"test_{uuid4().hex}"
            admin_engine = create_engine(_base_test_url(), future=True)
            try:
                with admin_engine.begin() as connection:
                    connection.execute(CreateSchema(schema))
            finally:
                admin_engine.dispose()
            _SCHEMA_BY_KEY[normalized_key] = schema

    # PostgreSQL may parallelize large catalog-reflection queries after the test
    # database has accumulated many intentionally preserved isolated schemas.
    # Parallel workers allocate dynamic shared-memory segments on the native
    # PostgreSQL host; constrain only this disposable test connection rather than
    # changing cluster-wide production-like settings or deleting test data.
    isolated_url = _base_test_url().update_query_dict(
        {
            "options": " ".join(
                (f"-csearch_path={schema}", _TEST_CONNECTION_PARALLELISM_OPTION)
            )
        }
    )
    return isolated_url.render_as_string(hide_password=False)
