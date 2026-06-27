"""Add account attribution records."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_add_account_attribution_records"
down_revision = "0009_add_trade_attribution_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trade_attribution_records") as batch_op:
        batch_op.add_column(sa.Column("account", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_trade_attribution_records_account",
        "trade_attribution_records",
        ["account"],
    )

    op.create_table(
        "account_attribution_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("start_account_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("end_account_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("start_position_snapshot_batch_id", sa.String(length=64), nullable=True),
        sa.Column("end_position_snapshot_batch_id", sa.String(length=64), nullable=True),
        sa.Column("start_asof", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_asof", sa.DateTime(timezone=True), nullable=False),
        sa.Column("starting_equity", sa.Float(), nullable=True),
        sa.Column("ending_equity", sa.Float(), nullable=True),
        sa.Column("equity_change", sa.Float(), nullable=True),
        sa.Column("starting_cash", sa.Float(), nullable=True),
        sa.Column("ending_cash", sa.Float(), nullable=True),
        sa.Column("cash_change", sa.Float(), nullable=True),
        sa.Column("price_pnl", sa.Float(), nullable=True),
        sa.Column("rebalance_pnl", sa.Float(), nullable=True),
        sa.Column("execution_shortfall", sa.Float(), nullable=True),
        sa.Column("traded_notional", sa.Float(), nullable=True),
        sa.Column("fill_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("residual_pnl", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_account_attribution_records_start_account_snapshot_id",
        "account_attribution_records",
        ["start_account_snapshot_id"],
    )
    op.create_index(
        "ix_account_attribution_records_end_account_snapshot_id",
        "account_attribution_records",
        ["end_account_snapshot_id"],
    )
    op.create_index("ix_account_attribution_records_run_id", "account_attribution_records", ["run_id"])
    op.create_index(
        "ix_account_attribution_records_profile_id",
        "account_attribution_records",
        ["profile_id"],
    )
    op.create_index("ix_account_attribution_records_broker", "account_attribution_records", ["broker"])
    op.create_index("ix_account_attribution_records_account", "account_attribution_records", ["account"])
    op.create_index(
        "ix_account_attribution_records_start_position_snapshot_batch_id",
        "account_attribution_records",
        ["start_position_snapshot_batch_id"],
    )
    op.create_index(
        "ix_account_attribution_records_end_position_snapshot_batch_id",
        "account_attribution_records",
        ["end_position_snapshot_batch_id"],
    )
    op.create_index(
        "ix_account_attribution_records_start_asof",
        "account_attribution_records",
        ["start_asof"],
    )
    op.create_index(
        "ix_account_attribution_records_end_asof",
        "account_attribution_records",
        ["end_asof"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_attribution_records_end_asof", table_name="account_attribution_records")
    op.drop_index("ix_account_attribution_records_start_asof", table_name="account_attribution_records")
    op.drop_index(
        "ix_account_attribution_records_end_position_snapshot_batch_id",
        table_name="account_attribution_records",
    )
    op.drop_index(
        "ix_account_attribution_records_start_position_snapshot_batch_id",
        table_name="account_attribution_records",
    )
    op.drop_index("ix_account_attribution_records_account", table_name="account_attribution_records")
    op.drop_index("ix_account_attribution_records_broker", table_name="account_attribution_records")
    op.drop_index("ix_account_attribution_records_profile_id", table_name="account_attribution_records")
    op.drop_index("ix_account_attribution_records_run_id", table_name="account_attribution_records")
    op.drop_index(
        "ix_account_attribution_records_end_account_snapshot_id",
        table_name="account_attribution_records",
    )
    op.drop_index(
        "ix_account_attribution_records_start_account_snapshot_id",
        table_name="account_attribution_records",
    )
    op.drop_table("account_attribution_records")

    op.drop_index("ix_trade_attribution_records_account", table_name="trade_attribution_records")
    with op.batch_alter_table("trade_attribution_records") as batch_op:
        batch_op.drop_column("account")
