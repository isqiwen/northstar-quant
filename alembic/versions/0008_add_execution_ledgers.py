"""Add execution ledger tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_add_execution_ledgers"
down_revision = "0007_add_strategy_and_account_ledgers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_plan_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("execution_planner_id", sa.String(length=64), nullable=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=True),
        sa.Column("current_qty", sa.Float(), nullable=True),
        sa.Column("target_qty", sa.Float(), nullable=True),
        sa.Column("latest_price", sa.Float(), nullable=True),
        sa.Column("execution_reference_price", sa.Float(), nullable=True),
        sa.Column("estimated_trade_value", sa.Float(), nullable=True),
        sa.Column("order_semantic", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("order_type", sa.String(length=16), nullable=True),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_execution_plan_records_run_id", "execution_plan_records", ["run_id"])
    op.create_index("ix_execution_plan_records_batch_id", "execution_plan_records", ["batch_id"])
    op.create_index("ix_execution_plan_records_plan_id", "execution_plan_records", ["plan_id"])
    op.create_index("ix_execution_plan_records_profile_id", "execution_plan_records", ["profile_id"])
    op.create_index(
        "ix_execution_plan_records_execution_planner_id",
        "execution_plan_records",
        ["execution_planner_id"],
    )
    op.create_index(
        "ix_execution_plan_records_strategy_id",
        "execution_plan_records",
        ["strategy_id"],
    )
    op.create_index("ix_execution_plan_records_symbol", "execution_plan_records", ["symbol"])

    op.create_table(
        "working_order_snapshot_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("open_order_snapshot_batch_id", sa.String(length=64), nullable=True),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("filled_qty", sa.Float(), nullable=True),
        sa.Column("remaining_qty", sa.Float(), nullable=True),
        sa.Column("avg_fill_price", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=True),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_working_order_snapshot_records_run_id",
        "working_order_snapshot_records",
        ["run_id"],
    )
    op.create_index(
        "ix_working_order_snapshot_records_profile_id",
        "working_order_snapshot_records",
        ["profile_id"],
    )
    op.create_index(
        "ix_working_order_snapshot_records_broker",
        "working_order_snapshot_records",
        ["broker"],
    )
    op.create_index(
        "ix_working_order_snapshot_records_account",
        "working_order_snapshot_records",
        ["account"],
    )
    op.create_index(
        "ix_working_order_snapshot_records_open_order_snapshot_batch_id",
        "working_order_snapshot_records",
        ["open_order_snapshot_batch_id"],
    )
    op.create_index(
        "ix_working_order_snapshot_records_broker_order_id",
        "working_order_snapshot_records",
        ["broker_order_id"],
    )
    op.create_index(
        "ix_working_order_snapshot_records_symbol",
        "working_order_snapshot_records",
        ["symbol"],
    )
    op.create_index(
        "ix_working_order_snapshot_records_status",
        "working_order_snapshot_records",
        ["status"],
    )
    op.create_index(
        "ix_working_order_snapshot_records_observed_at",
        "working_order_snapshot_records",
        ["observed_at"],
    )

    op.create_table(
        "cancel_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cancel_batch_id", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cancel_records_cancel_batch_id", "cancel_records", ["cancel_batch_id"])
    op.create_index("ix_cancel_records_order_id", "cancel_records", ["order_id"])
    op.create_index("ix_cancel_records_broker", "cancel_records", ["broker"])
    op.create_index("ix_cancel_records_broker_order_id", "cancel_records", ["broker_order_id"])
    op.create_index("ix_cancel_records_run_id", "cancel_records", ["run_id"])
    op.create_index("ix_cancel_records_profile_id", "cancel_records", ["profile_id"])
    op.create_index("ix_cancel_records_account", "cancel_records", ["account"])
    op.create_index("ix_cancel_records_status", "cancel_records", ["status"])
    op.create_index("ix_cancel_records_requested_at", "cancel_records", ["requested_at"])


def downgrade() -> None:
    op.drop_index("ix_cancel_records_requested_at", table_name="cancel_records")
    op.drop_index("ix_cancel_records_status", table_name="cancel_records")
    op.drop_index("ix_cancel_records_account", table_name="cancel_records")
    op.drop_index("ix_cancel_records_profile_id", table_name="cancel_records")
    op.drop_index("ix_cancel_records_run_id", table_name="cancel_records")
    op.drop_index("ix_cancel_records_broker_order_id", table_name="cancel_records")
    op.drop_index("ix_cancel_records_broker", table_name="cancel_records")
    op.drop_index("ix_cancel_records_order_id", table_name="cancel_records")
    op.drop_index("ix_cancel_records_cancel_batch_id", table_name="cancel_records")
    op.drop_table("cancel_records")

    op.drop_index(
        "ix_working_order_snapshot_records_observed_at",
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        "ix_working_order_snapshot_records_status",
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        "ix_working_order_snapshot_records_symbol",
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        "ix_working_order_snapshot_records_broker_order_id",
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        "ix_working_order_snapshot_records_open_order_snapshot_batch_id",
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        "ix_working_order_snapshot_records_account",
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        "ix_working_order_snapshot_records_broker",
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        "ix_working_order_snapshot_records_profile_id",
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        "ix_working_order_snapshot_records_run_id",
        table_name="working_order_snapshot_records",
    )
    op.drop_table("working_order_snapshot_records")

    op.drop_index("ix_execution_plan_records_symbol", table_name="execution_plan_records")
    op.drop_index("ix_execution_plan_records_strategy_id", table_name="execution_plan_records")
    op.drop_index(
        "ix_execution_plan_records_execution_planner_id",
        table_name="execution_plan_records",
    )
    op.drop_index("ix_execution_plan_records_profile_id", table_name="execution_plan_records")
    op.drop_index("ix_execution_plan_records_plan_id", table_name="execution_plan_records")
    op.drop_index("ix_execution_plan_records_batch_id", table_name="execution_plan_records")
    op.drop_index("ix_execution_plan_records_run_id", table_name="execution_plan_records")
    op.drop_table("execution_plan_records")
