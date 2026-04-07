"""初始化核心表结构。"""

from alembic import op
import sqlalchemy as sa

revision = "0001_init_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建系统运行所需的核心表。"""

    op.create_table(
        "run_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_logs_task_name", "run_logs", ["task_name"])
    op.create_index("ix_run_logs_status", "run_logs", ["status"])

    op.create_table(
        "signal_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("signal_value", sa.Float(), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=False),
        sa.Column("asof", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signal_records_strategy_id", "signal_records", ["strategy_id"])
    op.create_index("ix_signal_records_symbol", "signal_records", ["symbol"])
    op.create_index("ix_signal_records_asof", "signal_records", ["asof"])

    op.create_table(
        "order_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=True),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_order_records_strategy_id", "order_records", ["strategy_id"])
    op.create_index("ix_order_records_symbol", "order_records", ["symbol"])
    op.create_index("ix_order_records_status", "order_records", ["status"])

    op.create_table(
        "fill_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fill_records_order_id", "fill_records", ["order_id"])
    op.create_index("ix_fill_records_symbol", "fill_records", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_fill_records_symbol", table_name="fill_records")
    op.drop_index("ix_fill_records_order_id", table_name="fill_records")
    op.drop_table("fill_records")
    op.drop_index("ix_order_records_status", table_name="order_records")
    op.drop_index("ix_order_records_symbol", table_name="order_records")
    op.drop_index("ix_order_records_strategy_id", table_name="order_records")
    op.drop_table("order_records")
    op.drop_index("ix_signal_records_asof", table_name="signal_records")
    op.drop_index("ix_signal_records_symbol", table_name="signal_records")
    op.drop_index("ix_signal_records_strategy_id", table_name="signal_records")
    op.drop_table("signal_records")
    op.drop_index("ix_run_logs_status", table_name="run_logs")
    op.drop_index("ix_run_logs_task_name", table_name="run_logs")
    op.drop_table("run_logs")
