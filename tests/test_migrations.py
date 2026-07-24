from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
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
        assert _column_nullable(database_url, "order_records", "submitted_at") is True
        order_columns = {
            column["name"]
            for column in inspect(
                create_engine(database_url, future=True)
            ).get_columns("order_records")
        }
        assert {
                "broker",
                "instrument_id",
                "exchange_id",
            "currency",
            "idempotency_key",
            "request_fingerprint",
            "execution_policy_fingerprint",
            "attempt_no",
            "order_ref",
            "client_id",
            "perm_id",
            "filled_qty",
            "remaining_qty",
            "avg_fill_price",
            "submission_owner",
            "lease_fencing_token",
            "prepared_at",
            "submission_started_at",
            "broker_acknowledged_at",
            "updated_at",
        }.issubset(order_columns)
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
            "instrument_id",
        }.issubset(fill_columns)
        inspector = inspect(create_engine(database_url, future=True))
        assert "execution_lease_records" in inspector.get_table_names()
        unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("order_records")
        }
        assert {
            "uq_order_records_broker_account_idempotency_key",
            "uq_order_records_broker_account_order_ref",
            "uq_order_records_broker_account_perm_id",
            "uq_order_records_broker_account_client_order_id",
        }.issubset(unique_constraints)

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
                    'RB2405',
                    'BUY',
                    1,
                    100,
                    '2024-01-02 15:30:00'
                )
                """
            )

        with pytest.raises(NotImplementedError, match="不支持回滚"):
            command.downgrade(config, "0017_add_fill_broker_identity")
    finally:
        get_settings.cache_clear()
