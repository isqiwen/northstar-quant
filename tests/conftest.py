"""测试级公共夹具。"""

from __future__ import annotations

import os

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.support.database import (
    create_postgresql_session_factory,
    create_postgresql_test_engine,
)
from tests.support.postgresql import cleanup_postgresql_test_schemas

_TEST_LAYERS = ("unit", "integration", "contract", "e2e")
_DEFAULT_RUNTIME_DATABASE_URL = (
    "postgresql+psycopg://northstar@127.0.0.1:5432/northstar"
)


def _configure_safe_test_runtime_settings() -> None:
    """避免本地遗留 SQLite .env 在测试收集阶段阻断不访问数据库的测试。"""

    if os.getenv("NORTHSTAR_DATABASE_URL") is not None:
        return

    dotenv_database_url = dotenv_values(".env").get("NORTHSTAR_DATABASE_URL")
    if isinstance(dotenv_database_url, str) and dotenv_database_url.startswith(
        "postgresql+psycopg://"
    ):
        return

    # 仅为 pytest 进程提供语法正确的安全默认值；数据库测试仍会连接独立的
    # northstar_test，并在 PostgreSQL 不可用时按真实依赖失败，绝不回退 SQLite。
    os.environ["NORTHSTAR_DATABASE_URL"] = _DEFAULT_RUNTIME_DATABASE_URL


_configure_safe_test_runtime_settings()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """根据测试目录自动添加层级 marker。"""

    for item in items:
        relative_path = item.path.relative_to(item.config.rootpath)
        if len(relative_path.parts) < 2 or relative_path.parts[0] != "tests":
            continue
        layer = relative_path.parts[1]
        if layer in _TEST_LAYERS:
            item.add_marker(getattr(pytest.mark, layer))


@pytest.fixture
def postgresql_engine(request: pytest.FixtureRequest) -> Engine:
    """为单个测试提供独立 schema 和已建表的 PostgreSQL engine。"""

    engine = create_postgresql_test_engine(request.node.nodeid)
    yield engine
    engine.dispose()


@pytest.fixture
def postgresql_session_factory(
    postgresql_engine: Engine,
) -> sessionmaker[Session]:
    """为单个测试提供统一配置的 SQLAlchemy session factory。"""

    return create_postgresql_session_factory(postgresql_engine)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_isolated_postgresql_schemas():
    yield
    cleanup_postgresql_test_schemas()
