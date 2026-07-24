"""数据库初始化工具。"""

from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import DateTime, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

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
        "fill_records",
        "broker",
        "ALTER TABLE fill_records ADD COLUMN broker VARCHAR(32)",
    ),
    (
        "fill_records",
        "account",
        "ALTER TABLE fill_records ADD COLUMN account VARCHAR(64)",
    ),
    (
        "fill_records",
        "exec_id",
        "ALTER TABLE fill_records ADD COLUMN exec_id VARCHAR(128)",
    ),
    (
        "fill_records",
        "perm_id",
        "ALTER TABLE fill_records ADD COLUMN perm_id INTEGER",
    ),
    (
        "fill_records",
        "client_id",
        "ALTER TABLE fill_records ADD COLUMN client_id INTEGER",
    ),
    (
        "fill_records",
        "instrument_id",
        "ALTER TABLE fill_records ADD COLUMN instrument_id VARCHAR(32)",
    ),
    (
        "fill_records",
        "exchange_id",
        "ALTER TABLE fill_records ADD COLUMN exchange_id VARCHAR(16)",
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
        "broker",
        "ALTER TABLE order_records ADD COLUMN broker VARCHAR(32)",
    ),
    (
        "order_records",
        "instrument_id",
        "ALTER TABLE order_records ADD COLUMN instrument_id VARCHAR(32)",
    ),
    (
        "order_records",
        "exchange_id",
        "ALTER TABLE order_records ADD COLUMN exchange_id VARCHAR(16)",
    ),
    (
        "order_records",
        "currency",
        "ALTER TABLE order_records ADD COLUMN currency VARCHAR(8)",
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
        "order_records",
        "idempotency_key",
        "ALTER TABLE order_records ADD COLUMN idempotency_key VARCHAR(80)",
    ),
    (
        "order_records",
        "request_fingerprint",
        "ALTER TABLE order_records ADD COLUMN request_fingerprint VARCHAR(64)",
    ),
    (
        "order_records",
        "execution_policy_fingerprint",
        "ALTER TABLE order_records ADD COLUMN execution_policy_fingerprint VARCHAR(64)",
    ),
    (
        "order_records",
        "attempt_no",
        "ALTER TABLE order_records ADD COLUMN attempt_no INTEGER DEFAULT 1",
    ),
    (
        "order_records",
        "order_ref",
        "ALTER TABLE order_records ADD COLUMN order_ref VARCHAR(64)",
    ),
    (
        "order_records",
        "client_id",
        "ALTER TABLE order_records ADD COLUMN client_id INTEGER",
    ),
    (
        "order_records",
        "perm_id",
        "ALTER TABLE order_records ADD COLUMN perm_id INTEGER",
    ),
    (
        "order_records",
        "filled_qty",
        "ALTER TABLE order_records ADD COLUMN filled_qty FLOAT",
    ),
    (
        "order_records",
        "remaining_qty",
        "ALTER TABLE order_records ADD COLUMN remaining_qty FLOAT",
    ),
    (
        "order_records",
        "avg_fill_price",
        "ALTER TABLE order_records ADD COLUMN avg_fill_price FLOAT",
    ),
    (
        "order_records",
        "submission_owner",
        "ALTER TABLE order_records ADD COLUMN submission_owner VARCHAR(64)",
    ),
    (
        "order_records",
        "lease_fencing_token",
        "ALTER TABLE order_records ADD COLUMN lease_fencing_token INTEGER",
    ),
    (
        "order_records",
        "last_submission_error",
        "ALTER TABLE order_records ADD COLUMN last_submission_error TEXT",
    ),
    (
        "order_records",
        "prepared_at",
        "ALTER TABLE order_records ADD COLUMN prepared_at DATETIME",
    ),
    (
        "order_records",
        "submission_started_at",
        "ALTER TABLE order_records ADD COLUMN submission_started_at DATETIME",
    ),
    (
        "order_records",
        "broker_acknowledged_at",
        "ALTER TABLE order_records ADD COLUMN broker_acknowledged_at DATETIME",
    ),
    (
        "order_records",
        "updated_at",
        "ALTER TABLE order_records ADD COLUMN updated_at DATETIME",
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
        "other_non_trade_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN other_non_trade_cash_flow FLOAT",
    ),
    (
        "account_attribution_records",
        "total_non_trade_cash_flow",
        "ALTER TABLE account_attribution_records ADD COLUMN total_non_trade_cash_flow FLOAT",
    ),
)


def _redact_database_url(database_url: str) -> str:
    """返回可安全写入日志的数据库地址。"""

    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except (ArgumentError, ValueError):
        return "<invalid-database-url>"


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

    order_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("order_records")
    }
    submitted_at = order_columns.get("submitted_at")
    if submitted_at is not None and not bool(submitted_at["nullable"]):
        # Prepared 意图尚未触达券商，不能伪造 submitted_at。SQLite 不支持
        # 直接 DROP NOT NULL，使用 Alembic batch 安全重建旧表。
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table(
                "order_records",
                recreate="always",
            ) as batch_op:
                batch_op.alter_column(
                    "submitted_at",
                    existing_type=DateTime(),
                    nullable=True,
                )
        logger.info("已将本地 SQLite 旧订单表 submitted_at 调整为可空")

    # create_all 不会给既有表补约束。持久化幂等协议依赖数据库唯一仲裁，
    # 因此旧 SQLite 也必须具备与 Alembic 0018 等价的幂等与券商身份唯一索引。
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_order_records_broker_account_idempotency_key "
                "ON order_records (broker, account, idempotency_key)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_order_records_broker_account_order_ref "
                "ON order_records (broker, account, order_ref)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_order_records_broker_account_perm_id "
                "ON order_records (broker, account, perm_id)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_order_records_broker_account_client_order_id "
                "ON order_records (broker, account, client_id, broker_order_id)"
            )
        )


def init_db() -> None:
    """初始化数据库表结构。

    说明：
    - SQLite 下用于快速起步非常方便
    - PostgreSQL 正式使用时更推荐配合 Alembic
    """

    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "开始初始化数据库，database_url=%s",
        _redact_database_url(settings.database_url),
    )

    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    _patch_local_sqlite_schema(engine)
    logger.info("数据库表结构初始化完成")
