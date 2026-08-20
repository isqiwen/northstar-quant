"""测试 PostgreSQL 连接必须保持在不可与运行时混淆的本机隔离目标。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import postgresql
from tests.helpers.paths import PROJECT_ROOT


@pytest.mark.parametrize(
    "database_url",
    (
        "postgresql+psycopg://northstar@127.0.0.1:5432/northstar_test",
        "postgresql+psycopg://northstar@localhost:5432/northstar_test",
        "postgresql+psycopg://northstar@[::1]:5432/northstar_test",
    ),
)
def test_test_database_url_accepts_only_loopback_northstar_test(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NORTHSTAR_TEST_DATABASE_URL", database_url)

    url = postgresql._base_test_url()

    assert url.database == "northstar_test"


@pytest.mark.parametrize(
    "database_url",
    (
        "postgresql+psycopg://northstar@db.example.com:5432/northstar_test",
        "postgresql+psycopg://northstar@127.0.0.1:5432/northstar",
        "postgresql+psycopg://northstar@127.0.0.1:5432/northstar_test_backup",
        "postgresql://northstar@127.0.0.1:5432/northstar_test",
    ),
)
def test_test_database_url_fails_closed_outside_dedicated_loopback_target(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NORTHSTAR_TEST_DATABASE_URL", database_url)

    with pytest.raises(RuntimeError):
        postgresql._base_test_url()


def test_test_helper_has_no_automatic_schema_cleanup_api() -> None:
    """任何 schema 删除必须保留给用户手工操作，测试框架不得自动执行。"""

    assert not hasattr(postgresql, "cleanup_postgresql_test_schemas")
    sources = (
        Path(postgresql.__file__).read_text(encoding="utf-8").lower(),
        (PROJECT_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8").lower(),
    )
    forbidden = ("dropschema", "drop schema", "metadata.drop_all", "truncate", "delete from")

    for source in sources:
        assert not any(operation in source for operation in forbidden)
