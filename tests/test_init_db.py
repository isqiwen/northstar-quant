from sqlalchemy import create_engine, inspect

from northstar_quant.config.settings import get_settings
from northstar_quant.db.init_db import init_db


def test_init_db_patches_legacy_sqlite_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    storage_dir = tmp_path / "storage"

    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE order_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id VARCHAR(64) NOT NULL,
                symbol VARCHAR(32) NOT NULL,
                side VARCHAR(8) NOT NULL,
                qty FLOAT NOT NULL,
                target_weight FLOAT,
                broker_order_id VARCHAR(128),
                status VARCHAR(32) NOT NULL,
                submitted_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE fill_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                symbol VARCHAR(32) NOT NULL,
                qty FLOAT NOT NULL,
                price FLOAT NOT NULL,
                filled_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE position_snapshot_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account VARCHAR(64),
                symbol VARCHAR(32) NOT NULL,
                qty FLOAT NOT NULL,
                avg_cost FLOAT,
                market_price FLOAT,
                market_value FLOAT,
                asof DATETIME NOT NULL
            )
            """
        )

    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(storage_dir))
    get_settings.cache_clear()

    try:
        init_db()

        inspector = inspect(create_engine(f"sqlite:///{db_path.as_posix()}", future=True))
        table_names = set(inspector.get_table_names())
        order_columns = {column["name"] for column in inspector.get_columns("order_records")}
        fill_columns = {column["name"] for column in inspector.get_columns("fill_records")}
        position_columns = {
            column["name"] for column in inspector.get_columns("position_snapshot_records")
        }
        account_snapshot_columns = {
            column["name"] for column in inspector.get_columns("account_snapshot_records")
        }
        trade_attribution_columns = {
            column["name"] for column in inspector.get_columns("trade_attribution_records")
        }
        account_attribution_columns = {
            column["name"] for column in inspector.get_columns("account_attribution_records")
        }

        assert "order_semantic" in order_columns
        assert "profile_id" in order_columns
        assert "order_type" in order_columns
        assert "limit_price" in order_columns
        assert "reason" in order_columns
        assert "account" in order_columns
        assert "reference_price" in order_columns
        assert "reference_price_source" in order_columns
        assert "planned_trade_value" in order_columns
        assert "execution_planner_id" in order_columns
        assert "run_id" in order_columns
        assert "batch_id" in order_columns
        assert "plan_id" in order_columns
        assert "broker_order_id" in fill_columns
        assert "side" in fill_columns
        assert "snapshot_batch_id" in position_columns
        assert "strategy_run_records" in table_names
        assert "strategy_snapshot_records" in table_names
        assert "account_snapshot_records" in table_names
        assert "execution_plan_records" in table_names
        assert "working_order_snapshot_records" in table_names
        assert "cancel_records" in table_names
        assert "trade_attribution_records" in table_names
        assert "account_attribution_records" in table_names
        assert "anomaly_event_records" in table_names
        assert "run_health_records" in table_names
        assert "account_values_json" in account_snapshot_columns
        assert "account" in trade_attribution_columns
        assert "dividend_cash_flow" in account_attribution_columns
        assert "interest_cash_flow" in account_attribution_columns
        assert "fee_cash_flow" in account_attribution_columns
        assert "tax_cash_flow" in account_attribution_columns
        assert "funding_cash_flow" in account_attribution_columns
        assert "corporate_action_cash_flow" in account_attribution_columns
        assert "other_non_trade_cash_flow" in account_attribution_columns
        assert "total_non_trade_cash_flow" in account_attribution_columns
    finally:
        get_settings.cache_clear()
