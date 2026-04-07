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

    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(storage_dir))
    get_settings.cache_clear()

    try:
        init_db()

        inspector = inspect(create_engine(f"sqlite:///{db_path.as_posix()}", future=True))
        order_columns = {column["name"] for column in inspector.get_columns("order_records")}
        fill_columns = {column["name"] for column in inspector.get_columns("fill_records")}

        assert "order_semantic" in order_columns
        assert "broker_order_id" in fill_columns
        assert "side" in fill_columns
    finally:
        get_settings.cache_clear()
