"""SQLite Local tools 不能越过 PostgreSQL、Alembic 或 Lake 验证边界。"""

from tests.helpers.paths import PROJECT_ROOT


def test_sqlite_local_lake_index_is_isolated_rebuildable_and_non_authoritative():
    source = (PROJECT_ROOT / "src/northstar_quant/data/lake/local_index.py").read_text(
        encoding="utf-8"
    )

    assert "import sqlite3" in source
    assert "lake_store.verify(reference)" in source
    assert "BEGIN IMMEDIATE" in source
    assert "PRAGMA busy_timeout = 5000" in source
    assert "corrupt-" in source
    assert "DELETE FROM" not in source
    assert "DROP TABLE" not in source
    for forbidden in (
        "foundation.db",
        "sqlalchemy",
        "alembic",
        "database_url",
        "NORTHSTAR_DATABASE_URL",
        "trading_execution",
        "portfolio_risk",
        "init_db",
    ):
        assert forbidden not in source
