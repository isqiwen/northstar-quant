"""测试级公共夹具。"""

from __future__ import annotations

import os

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers.database import (
    create_postgresql_session_factory,
    create_postgresql_test_engine,
)
from tests.helpers.paths import PROJECT_ROOT

_DOMAIN_TEST_ROOTS = frozenset(
    {
        "data",
        "intelligence",
        "research",
        "portfolio_risk",
        "trading_execution",
        "foundation",
    }
)
_TEST_CATEGORY_MARKERS = frozenset(
    {
        "unit",
        "integration",
        "contract",
        "e2e",
        "golden",
        "regression",
        "statistical",
        "scenario",
        "simulation",
        "failure",
    }
)
_DEFAULT_RUNTIME_DATABASE_URL = (
    "postgresql+psycopg://northstar@127.0.0.1:5432/northstar"
)


def _configure_safe_test_runtime_settings() -> None:
    """避免本地遗留 SQLite .env 在测试收集阶段阻断不访问数据库的测试。"""

    if os.getenv("NORTHSTAR_DATABASE_URL") is not None:
        return

    dotenv_database_url = dotenv_values(PROJECT_ROOT / ".env").get(
        "NORTHSTAR_DATABASE_URL"
    )
    if isinstance(dotenv_database_url, str) and dotenv_database_url.startswith(
        "postgresql+psycopg://"
    ):
        return

    # 仅为 pytest 进程提供语法正确的安全默认值；数据库测试仍会连接独立的
    # northstar_test，并在 PostgreSQL 不可用时按真实依赖失败，绝不回退 SQLite。
    os.environ["NORTHSTAR_DATABASE_URL"] = _DEFAULT_RUNTIME_DATABASE_URL


_configure_safe_test_runtime_settings()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """根据领域优先的测试目录自动添加分类 marker。"""

    for item in items:
        relative_path = item.path.relative_to(item.config.rootpath)
        if len(relative_path.parts) < 2 or relative_path.parts[0] != "tests":
            continue
        root = relative_path.parts[1]
        category: str | None = None
        if root == "architecture":
            category = "contract"
        elif root in _TEST_CATEGORY_MARKERS:
            category = root
        elif root in _DOMAIN_TEST_ROOTS and len(relative_path.parts) >= 3:
            candidate = relative_path.parts[2]
            if candidate in _TEST_CATEGORY_MARKERS:
                category = candidate

        if category is not None:
            item.add_marker(getattr(pytest.mark, category))


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
