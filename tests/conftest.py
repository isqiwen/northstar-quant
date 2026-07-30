"""测试级公共夹具。"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.support.database import (
    create_postgresql_session_factory,
    create_postgresql_test_engine,
)
from tests.support.postgresql import cleanup_postgresql_test_schemas

_TEST_LAYERS = ("unit", "integration", "contract", "e2e")


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
