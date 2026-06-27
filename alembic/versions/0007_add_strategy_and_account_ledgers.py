"""Add strategy and account ledger tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_add_strategy_and_account_ledgers"
down_revision = "0006_add_order_audit_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_run_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("pipeline_strategy_id", sa.String(length=128), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("selected_strategy_ids_json", sa.Text(), nullable=True),
        sa.Column("strategy_params_json", sa.Text(), nullable=True),
        sa.Column("risk_limits_json", sa.Text(), nullable=True),
        sa.Column("market_data_asof", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signal_data_asof", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_asof", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_strategy_run_records_run_id", "strategy_run_records", ["run_id"])
    op.create_index("ix_strategy_run_records_profile_id", "strategy_run_records", ["profile_id"])
    op.create_index(
        "ix_strategy_run_records_pipeline_strategy_id",
        "strategy_run_records",
        ["pipeline_strategy_id"],
    )
    op.create_index(
        "ix_strategy_run_records_output_type",
        "strategy_run_records",
        ["output_type"],
    )
    op.create_index("ix_strategy_run_records_output_asof", "strategy_run_records", ["output_asof"])

    op.create_table(
        "strategy_snapshot_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("pipeline_strategy_id", sa.String(length=128), nullable=False),
        sa.Column("source_strategy_id", sa.String(length=64), nullable=True),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("signal_value", sa.Float(), nullable=True),
        sa.Column("target_weight", sa.Float(), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=True),
        sa.Column("size_fraction", sa.Float(), nullable=True),
        sa.Column("order_semantic", sa.String(length=16), nullable=True),
        sa.Column("order_type", sa.String(length=16), nullable=True),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("asof", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_strategy_snapshot_records_run_id", "strategy_snapshot_records", ["run_id"])
    op.create_index(
        "ix_strategy_snapshot_records_profile_id",
        "strategy_snapshot_records",
        ["profile_id"],
    )
    op.create_index(
        "ix_strategy_snapshot_records_pipeline_strategy_id",
        "strategy_snapshot_records",
        ["pipeline_strategy_id"],
    )
    op.create_index(
        "ix_strategy_snapshot_records_source_strategy_id",
        "strategy_snapshot_records",
        ["source_strategy_id"],
    )
    op.create_index(
        "ix_strategy_snapshot_records_output_type",
        "strategy_snapshot_records",
        ["output_type"],
    )
    op.create_index("ix_strategy_snapshot_records_symbol", "strategy_snapshot_records", ["symbol"])
    op.create_index("ix_strategy_snapshot_records_asof", "strategy_snapshot_records", ["asof"])

    op.create_table(
        "account_snapshot_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("position_snapshot_batch_id", sa.String(length=64), nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cash_balance", sa.Float(), nullable=True),
        sa.Column("net_liquidation", sa.Float(), nullable=True),
        sa.Column("gross_position_value", sa.Float(), nullable=True),
        sa.Column("net_position_value", sa.Float(), nullable=True),
        sa.Column("available_funds", sa.Float(), nullable=True),
        sa.Column("gross_exposure", sa.Float(), nullable=True),
        sa.Column("net_exposure", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("asof", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_snapshot_records_run_id", "account_snapshot_records", ["run_id"])
    op.create_index(
        "ix_account_snapshot_records_profile_id",
        "account_snapshot_records",
        ["profile_id"],
    )
    op.create_index("ix_account_snapshot_records_broker", "account_snapshot_records", ["broker"])
    op.create_index("ix_account_snapshot_records_account", "account_snapshot_records", ["account"])
    op.create_index(
        "ix_account_snapshot_records_position_snapshot_batch_id",
        "account_snapshot_records",
        ["position_snapshot_batch_id"],
    )
    op.create_index("ix_account_snapshot_records_asof", "account_snapshot_records", ["asof"])


def downgrade() -> None:
    op.drop_index("ix_account_snapshot_records_asof", table_name="account_snapshot_records")
    op.drop_index(
        "ix_account_snapshot_records_position_snapshot_batch_id",
        table_name="account_snapshot_records",
    )
    op.drop_index("ix_account_snapshot_records_account", table_name="account_snapshot_records")
    op.drop_index("ix_account_snapshot_records_broker", table_name="account_snapshot_records")
    op.drop_index("ix_account_snapshot_records_profile_id", table_name="account_snapshot_records")
    op.drop_index("ix_account_snapshot_records_run_id", table_name="account_snapshot_records")
    op.drop_table("account_snapshot_records")

    op.drop_index("ix_strategy_snapshot_records_asof", table_name="strategy_snapshot_records")
    op.drop_index("ix_strategy_snapshot_records_symbol", table_name="strategy_snapshot_records")
    op.drop_index(
        "ix_strategy_snapshot_records_output_type",
        table_name="strategy_snapshot_records",
    )
    op.drop_index(
        "ix_strategy_snapshot_records_source_strategy_id",
        table_name="strategy_snapshot_records",
    )
    op.drop_index(
        "ix_strategy_snapshot_records_pipeline_strategy_id",
        table_name="strategy_snapshot_records",
    )
    op.drop_index(
        "ix_strategy_snapshot_records_profile_id",
        table_name="strategy_snapshot_records",
    )
    op.drop_index("ix_strategy_snapshot_records_run_id", table_name="strategy_snapshot_records")
    op.drop_table("strategy_snapshot_records")

    op.drop_index("ix_strategy_run_records_output_asof", table_name="strategy_run_records")
    op.drop_index(
        "ix_strategy_run_records_output_type",
        table_name="strategy_run_records",
    )
    op.drop_index(
        "ix_strategy_run_records_pipeline_strategy_id",
        table_name="strategy_run_records",
    )
    op.drop_index("ix_strategy_run_records_profile_id", table_name="strategy_run_records")
    op.drop_index("ix_strategy_run_records_run_id", table_name="strategy_run_records")
    op.drop_table("strategy_run_records")
