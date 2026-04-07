"""新增真实持仓快照与券商同步日志表。"""

from alembic import op
import sqlalchemy as sa

revision = "0002_add_positions_and_broker_sync"
down_revision = "0001_init_core_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_snapshot_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("avg_cost", sa.Float(), nullable=True),
        sa.Column("market_price", sa.Float(), nullable=True),
        sa.Column("market_value", sa.Float(), nullable=True),
        sa.Column("asof", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_position_snapshot_records_account", "position_snapshot_records", ["account"])
    op.create_index("ix_position_snapshot_records_symbol", "position_snapshot_records", ["symbol"])
    op.create_index("ix_position_snapshot_records_asof", "position_snapshot_records", ["asof"])

    op.create_table(
        "broker_sync_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("sync_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_broker_sync_logs_broker", "broker_sync_logs", ["broker"])
    op.create_index("ix_broker_sync_logs_sync_type", "broker_sync_logs", ["sync_type"])
    op.create_index("ix_broker_sync_logs_status", "broker_sync_logs", ["status"])

    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.add_column(sa.Column("broker_order_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("side", sa.String(length=8), nullable=True))
    op.create_index("ix_fill_records_broker_order_id", "fill_records", ["broker_order_id"])


def downgrade() -> None:
    op.drop_index("ix_fill_records_broker_order_id", table_name="fill_records")
    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.drop_column("side")
        batch_op.drop_column("broker_order_id")

    op.drop_index("ix_broker_sync_logs_status", table_name="broker_sync_logs")
    op.drop_index("ix_broker_sync_logs_sync_type", table_name="broker_sync_logs")
    op.drop_index("ix_broker_sync_logs_broker", table_name="broker_sync_logs")
    op.drop_table("broker_sync_logs")

    op.drop_index("ix_position_snapshot_records_asof", table_name="position_snapshot_records")
    op.drop_index("ix_position_snapshot_records_symbol", table_name="position_snapshot_records")
    op.drop_index("ix_position_snapshot_records_account", table_name="position_snapshot_records")
    op.drop_table("position_snapshot_records")
