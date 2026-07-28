"""测试级公共夹具。"""

from __future__ import annotations

import pytest

from tests.postgresql import cleanup_postgresql_test_schemas


@pytest.fixture(scope="session", autouse=True)
def _cleanup_isolated_postgresql_schemas():
    yield
    cleanup_postgresql_test_schemas()
