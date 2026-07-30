"""PostgreSQL 测试 schema 隔离工具。"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.schema import CreateSchema, DropSchema

_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://northstar@127.0.0.1:5432/northstar_test"
)
_SCHEMA_BY_KEY: dict[str, str] = {}
_LOCK = Lock()


def _base_test_url() -> URL:
    dotenv_database_url = dotenv_values(".env").get(
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
    return url


def postgresql_test_url(key: str | Path) -> str:
    """为一个测试键创建并复用独立 PostgreSQL schema。"""

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

    isolated_url = _base_test_url().update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    return isolated_url.render_as_string(hide_password=False)


def cleanup_postgresql_test_schemas() -> None:
    """删除本次测试进程创建的全部隔离 schema。"""

    with _LOCK:
        schemas = list(_SCHEMA_BY_KEY.values())
        _SCHEMA_BY_KEY.clear()
    if not schemas:
        return

    admin_engine = create_engine(_base_test_url(), future=True)
    try:
        for schema in schemas:
            with admin_engine.begin() as connection:
                connection.execute(
                    DropSchema(schema, cascade=True, if_exists=True)
                )
    finally:
        admin_engine.dispose()
