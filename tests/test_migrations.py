from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from tests.postgresql import postgresql_test_url

from northstar_quant.config.settings import get_settings
from northstar_quant.db import models  # noqa: F401
from northstar_quant.db.base import Base


def _alembic_config(project_root: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    return config


def test_initial_migration_matches_current_orm_and_can_downgrade(
    tmp_path,
    monkeypatch,
):
    project_root = Path(__file__).resolve().parents[1]
    database_url = postgresql_test_url(tmp_path / "migration.db")
    config = _alembic_config(project_root)

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
            actual_columns = {
                column["name"]
                for column in inspector.get_columns(table_name)
            }
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
                constraint["name"]
                for constraint in inspector.get_unique_constraints(table_name)
            }
            assert actual_unique_constraints == expected_unique_constraints

        fill_columns = {
            column["name"]: column
            for column in inspector.get_columns("fill_records")
        }
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
        command.downgrade(config, "base")

        remaining_tables = set(inspect(engine).get_table_names()) - {
            "alembic_version"
        }
        assert remaining_tables == set()
    finally:
        get_settings.cache_clear()
