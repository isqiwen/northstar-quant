"""数据库初始化工具。"""

from __future__ import annotations

from sqlalchemy import inspect, text

from northstar_quant.config.settings import get_settings
from northstar_quant.db.base import Base
from northstar_quant.db.session import make_engine
from northstar_quant.db import models  # noqa: F401
from northstar_quant.logging_.logger import get_logger

logger = get_logger(__name__, command="init-db")

_SQLITE_ADDITIVE_PATCHES: tuple[tuple[str, str, str], ...] = (
    (
        "fill_records",
        "broker_order_id",
        "ALTER TABLE fill_records ADD COLUMN broker_order_id VARCHAR(128)",
    ),
    (
        "fill_records",
        "side",
        "ALTER TABLE fill_records ADD COLUMN side VARCHAR(8)",
    ),
    (
        "order_records",
        "order_semantic",
        "ALTER TABLE order_records ADD COLUMN order_semantic VARCHAR(16)",
    ),
)


def _patch_local_sqlite_schema(engine) -> None:
    """Apply safe additive schema patches for legacy local SQLite databases."""

    if engine.dialect.name != "sqlite":
        return

    table_names = set(inspect(engine).get_table_names())
    if not table_names:
        return

    with engine.begin() as connection:
        for table_name, column_name, ddl in _SQLITE_ADDITIVE_PATCHES:
            if table_name not in table_names:
                continue
            current_columns = {
                column["name"] for column in inspect(engine).get_columns(table_name)
            }
            if column_name in current_columns:
                continue
            connection.execute(text(ddl))
            logger.bind(table=table_name, column=column_name).info(
                "已为本地 SQLite 旧表补齐字段"
            )


def init_db() -> None:
    """初始化数据库表结构。

    说明：
    - SQLite 下用于快速起步非常方便
    - PostgreSQL 正式使用时更推荐配合 Alembic
    """

    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    logger.info("开始初始化数据库，database_url=%s", settings.database_url)

    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    _patch_local_sqlite_schema(engine)
    logger.info("数据库表结构初始化完成")
