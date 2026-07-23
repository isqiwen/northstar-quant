from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from northstar_quant.config.settings import get_settings


def _column_nullable(database_url: str, table_name: str, column_name: str) -> bool:
    inspector = inspect(create_engine(database_url, future=True))
    columns = {
        column["name"]: bool(column["nullable"])
        for column in inspector.get_columns(table_name)
    }
    return columns[column_name]


def test_fill_order_id_becomes_nullable_at_migration_head(tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))

    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        command.upgrade(config, "0014_add_run_health_records")
        assert _column_nullable(database_url, "fill_records", "order_id") is False

        command.upgrade(config, "head")
        assert _column_nullable(database_url, "fill_records", "order_id") is True
        order_columns = {
            column["name"]
            for column in inspect(
                create_engine(database_url, future=True)
            ).get_columns("order_records")
        }
        assert "broker" in order_columns
        fill_columns = {
            column["name"]
            for column in inspect(
                create_engine(database_url, future=True)
            ).get_columns("fill_records")
        }
        assert {
            "broker",
            "account",
            "exec_id",
            "perm_id",
            "client_id",
            "con_id",
        }.issubset(fill_columns)

        engine = create_engine(database_url, future=True)
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
                    'SPY',
                    'BUY',
                    1,
                    100,
                    '2024-01-02 15:30:00'
                )
                """
            )
    finally:
        get_settings.cache_clear()
