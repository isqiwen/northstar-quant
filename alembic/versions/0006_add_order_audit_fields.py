"""Add richer order audit fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_add_order_audit_fields"
down_revision = "0005_add_position_snapshot_batch_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("order_records") as batch_op:
        batch_op.add_column(sa.Column("profile_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("order_type", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("limit_price", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("account", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("reference_price", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("reference_price_source", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(sa.Column("planned_trade_value", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("execution_planner_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("run_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("batch_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("plan_id", sa.String(length=64), nullable=True))

    op.create_index("ix_order_records_profile_id", "order_records", ["profile_id"])
    op.create_index("ix_order_records_account", "order_records", ["account"])
    op.create_index("ix_order_records_run_id", "order_records", ["run_id"])
    op.create_index("ix_order_records_batch_id", "order_records", ["batch_id"])
    op.create_index("ix_order_records_plan_id", "order_records", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_order_records_plan_id", table_name="order_records")
    op.drop_index("ix_order_records_batch_id", table_name="order_records")
    op.drop_index("ix_order_records_run_id", table_name="order_records")
    op.drop_index("ix_order_records_account", table_name="order_records")
    op.drop_index("ix_order_records_profile_id", table_name="order_records")

    with op.batch_alter_table("order_records") as batch_op:
        batch_op.drop_column("plan_id")
        batch_op.drop_column("batch_id")
        batch_op.drop_column("run_id")
        batch_op.drop_column("execution_planner_id")
        batch_op.drop_column("planned_trade_value")
        batch_op.drop_column("reference_price_source")
        batch_op.drop_column("reference_price")
        batch_op.drop_column("account")
        batch_op.drop_column("reason")
        batch_op.drop_column("limit_price")
        batch_op.drop_column("order_type")
        batch_op.drop_column("profile_id")
