"""Add trade attribution records."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_add_trade_attribution_records"
down_revision = "0008_add_execution_ledgers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_attribution_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fill_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=True),
        sa.Column("execution_planner_id", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("fill_price", sa.Float(), nullable=False),
        sa.Column("reference_price", sa.Float(), nullable=False),
        sa.Column("reference_price_source", sa.String(length=32), nullable=True),
        sa.Column("actual_notional", sa.Float(), nullable=False),
        sa.Column("reference_notional", sa.Float(), nullable=False),
        sa.Column("implementation_shortfall", sa.Float(), nullable=False),
        sa.Column("implementation_shortfall_bps", sa.Float(), nullable=True),
        sa.Column("order_semantic", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trade_attribution_records_fill_id", "trade_attribution_records", ["fill_id"])
    op.create_index("ix_trade_attribution_records_order_id", "trade_attribution_records", ["order_id"])
    op.create_index(
        "ix_trade_attribution_records_broker_order_id",
        "trade_attribution_records",
        ["broker_order_id"],
    )
    op.create_index("ix_trade_attribution_records_run_id", "trade_attribution_records", ["run_id"])
    op.create_index("ix_trade_attribution_records_batch_id", "trade_attribution_records", ["batch_id"])
    op.create_index("ix_trade_attribution_records_plan_id", "trade_attribution_records", ["plan_id"])
    op.create_index(
        "ix_trade_attribution_records_profile_id",
        "trade_attribution_records",
        ["profile_id"],
    )
    op.create_index(
        "ix_trade_attribution_records_strategy_id",
        "trade_attribution_records",
        ["strategy_id"],
    )
    op.create_index(
        "ix_trade_attribution_records_execution_planner_id",
        "trade_attribution_records",
        ["execution_planner_id"],
    )
    op.create_index("ix_trade_attribution_records_symbol", "trade_attribution_records", ["symbol"])
    op.create_index(
        "ix_trade_attribution_records_attributed_at",
        "trade_attribution_records",
        ["attributed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trade_attribution_records_attributed_at", table_name="trade_attribution_records")
    op.drop_index("ix_trade_attribution_records_symbol", table_name="trade_attribution_records")
    op.drop_index(
        "ix_trade_attribution_records_execution_planner_id",
        table_name="trade_attribution_records",
    )
    op.drop_index("ix_trade_attribution_records_strategy_id", table_name="trade_attribution_records")
    op.drop_index("ix_trade_attribution_records_profile_id", table_name="trade_attribution_records")
    op.drop_index("ix_trade_attribution_records_plan_id", table_name="trade_attribution_records")
    op.drop_index("ix_trade_attribution_records_batch_id", table_name="trade_attribution_records")
    op.drop_index("ix_trade_attribution_records_run_id", table_name="trade_attribution_records")
    op.drop_index(
        "ix_trade_attribution_records_broker_order_id",
        table_name="trade_attribution_records",
    )
    op.drop_index("ix_trade_attribution_records_order_id", table_name="trade_attribution_records")
    op.drop_index("ix_trade_attribution_records_fill_id", table_name="trade_attribution_records")
    op.drop_table("trade_attribution_records")
