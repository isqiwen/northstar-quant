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
    (
        "order_records",
        "profile_id",
        "ALTER TABLE order_records ADD COLUMN profile_id VARCHAR(64)",
    ),
    (
        "order_records",
        "order_type",
        "ALTER TABLE order_records ADD COLUMN order_type VARCHAR(16)",
    ),
    (
        "order_records",
        "limit_price",
        "ALTER TABLE order_records ADD COLUMN limit_price FLOAT",
    ),
    (
        "order_records",
        "reason",
        "ALTER TABLE order_records ADD COLUMN reason TEXT",
    ),
    (
        "order_records",
        "account",
        "ALTER TABLE order_records ADD COLUMN account VARCHAR(64)",
    ),
    (
        "order_records",
        "reference_price",
        "ALTER TABLE order_records ADD COLUMN reference_price FLOAT",
    ),
    (
        "order_records",
        "reference_price_source",
        "ALTER TABLE order_records ADD COLUMN reference_price_source VARCHAR(32)",
    ),
    (
        "order_records",
        "planned_trade_value",
        "ALTER TABLE order_records ADD COLUMN planned_trade_value FLOAT",
    ),
    (
        "order_records",
        "execution_planner_id",
        "ALTER TABLE order_records ADD COLUMN execution_planner_id VARCHAR(64)",
    ),
    (
        "order_records",
        "run_id",
        "ALTER TABLE order_records ADD COLUMN run_id VARCHAR(64)",
    ),
    (
        "order_records",
        "batch_id",
        "ALTER TABLE order_records ADD COLUMN batch_id VARCHAR(64)",
    ),
    (
        "order_records",
        "plan_id",
        "ALTER TABLE order_records ADD COLUMN plan_id VARCHAR(64)",
    ),
    (
        "position_snapshot_records",
        "snapshot_batch_id",
        "ALTER TABLE position_snapshot_records ADD COLUMN snapshot_batch_id VARCHAR(64)",
    ),
    (
        "account_snapshot_records",
        "account_values_json",
        "ALTER TABLE account_snapshot_records ADD COLUMN account_values_json TEXT",
    ),
    (
        "trade_attribution_records",
        "account",
        "ALTER TABLE trade_attribution_records ADD COLUMN account VARCHAR(64)",
    ),
    (
        "account_attribution_records",
        "dividend_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN dividend_cash_flow FLOAT",
    ),
    (
        "account_attribution_records",
        "interest_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN interest_cash_flow FLOAT",
    ),
    (
        "account_attribution_records",
        "fee_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN fee_cash_flow FLOAT",
    ),
    (
        "account_attribution_records",
        "tax_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN tax_cash_flow FLOAT",
    ),
    (
        "account_attribution_records",
        "funding_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN funding_cash_flow FLOAT",
    ),
    (
        "account_attribution_records",
        "corporate_action_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN corporate_action_cash_flow FLOAT",
    ),
    (
        "account_attribution_records",
        "other_non_trade_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN other_non_trade_cash_flow FLOAT",
    ),
    (
        "account_attribution_records",
        "total_non_trade_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN total_non_trade_cash_flow FLOAT",
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
