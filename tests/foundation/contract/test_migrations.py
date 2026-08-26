from __future__ import annotations

import ast
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from tests.helpers.paths import PROJECT_ROOT
from tests.helpers.postgresql import postgresql_test_url

from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.db import models  # noqa: F401
from northstar_quant.foundation.db.base import Base


def _alembic_config(project_root: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    return config


def test_development_schema_uses_one_current_baseline_revision() -> None:
    config = _alembic_config(PROJECT_ROOT)
    script_directory = ScriptDirectory.from_config(config)
    migration_files = sorted((PROJECT_ROOT / "alembic" / "versions").glob("*.py"))

    assert [path.name for path in migration_files] == ["0001_current_schema_baseline.py"]
    assert script_directory.get_heads() == ["0001_current_schema_baseline"]

    source = migration_files[0].read_text(encoding="utf-8")
    assert 'revision = "0001_current_schema_baseline"' in source
    assert "down_revision = None" in source


def test_initial_migration_matches_current_orm_and_repeated_upgrade_preserves_data(
    tmp_path,
    monkeypatch,
):
    project_root = PROJECT_ROOT
    database_url = postgresql_test_url(tmp_path / "migration.db")
    config = _alembic_config(project_root)
    engine = None

    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        command.upgrade(config, "head")

        engine = create_engine(database_url, future=True)
        inspector = inspect(engine)
        expected_tables = set(Base.metadata.tables)
        actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert actual_tables == expected_tables

        for table_name, table in Base.metadata.tables.items():
            expected_columns = set(table.columns.keys())
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert actual_columns == expected_columns

            expected_indexes = {index.name for index in table.indexes}
            actual_indexes = {
                index["name"]
                for index in inspector.get_indexes(table_name)
                if not index.get("duplicates_constraint")
            }
            assert actual_indexes == expected_indexes

            expected_unique_constraints = {
                constraint.name
                for constraint in table.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            }
            actual_unique_constraints = {
                constraint["name"] for constraint in inspector.get_unique_constraints(table_name)
            }
            assert actual_unique_constraints == expected_unique_constraints

        fill_columns = {column["name"]: column for column in inspector.get_columns("fill_records")}
        assert fill_columns["order_id"]["nullable"] is True

        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO fill_records (
                    order_id,
                    broker_order_id,
                    symbol,
                    side,
                    qty,
                    price,
                    filled_at
                ) VALUES (
                    NULL,
                    'external-fill-001',
                    'RB2405',
                    'BUY',
                    1,
                    100,
                    '2024-01-02 15:30:00'
                )
                """
            )

        command.check(config)
        command.upgrade(config, "head")

        with engine.connect() as connection:
            fill_count = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM fill_records WHERE broker_order_id = 'external-fill-001'"
                )
            )
        assert fill_count == 1

        remaining_tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert remaining_tables == expected_tables
    finally:
        if engine is not None:
            engine.dispose()
        get_settings.cache_clear()


_DESTRUCTIVE_SQL = re.compile(
    r"\b(?:drop\s+(?:database|schema|table|index|column)|truncate|delete\s+from)\b",
    re.IGNORECASE,
)


def _migration_function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"迁移文件缺少 {name}：{path.name}")


def _migration_upgrade_functions(path: Path) -> list[ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and (node.name == "upgrade" or node.name.startswith("_apply_"))
    ]
    assert functions, f"迁移文件缺少 upgrade()：{path.name}"
    return functions


def _literal_sql_arguments(call: ast.Call) -> list[str]:
    values: list[str] = []
    for argument in call.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            values.append(argument.value)
    return values


def test_migration_upgrades_are_forward_only_and_preserve_database_objects() -> None:
    """常规初始化只允许前向扩展，不能在 upgrade 中清空或删除数据库对象。"""

    violations: list[str] = []
    migration_directory = PROJECT_ROOT / "alembic" / "versions"
    for path in sorted(migration_directory.glob("*.py")):
        for upgrade in _migration_upgrade_functions(path):
            for node in ast.walk(upgrade):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                receiver = node.func.value
                operation = node.func.attr
                if isinstance(receiver, ast.Name) and receiver.id == "op":
                    if operation.startswith("drop_"):
                        violations.append(f"{path.name}: op.{operation}")
                    if operation == "execute":
                        for sql in _literal_sql_arguments(node):
                            if _DESTRUCTIVE_SQL.search(sql):
                                violations.append(f"{path.name}: destructive SQL in op.execute")

    assert not violations, "前向迁移不得删除或清空数据库对象：" + "; ".join(violations)


def test_application_database_initialization_never_invokes_alembic_downgrade() -> None:
    """公共 init-db 入口只能前向升级，不能成为自动清库路径。"""

    init_db_path = PROJECT_ROOT / "src" / "northstar_quant" / "foundation" / "db" / "init_db.py"
    source = init_db_path.read_text(encoding="utf-8")

    assert "command.upgrade(" in source
    assert "command.downgrade(" not in source
