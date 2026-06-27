"""Add run health records."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014_add_run_health_records"
down_revision = "0013_add_anomaly_event_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_health_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("preflight_can_trade", sa.Boolean(), nullable=False),
        sa.Column("blocking_failure_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("target_symbol_count", sa.Integer(), nullable=False),
        sa.Column("target_weight_sum", sa.Float(), nullable=True),
        sa.Column("execution_plan_count", sa.Integer(), nullable=False),
        sa.Column("planned_trade_value", sa.Float(), nullable=True),
        sa.Column("plan_consistency_issue_count", sa.Integer(), nullable=False),
        sa.Column("open_order_count", sa.Integer(), nullable=False),
        sa.Column("partial_fill_count", sa.Integer(), nullable=False),
        sa.Column("fills_seen_count", sa.Integer(), nullable=False),
        sa.Column("execution_shortfall", sa.Float(), nullable=True),
        sa.Column("execution_shortfall_bps", sa.Float(), nullable=True),
        sa.Column("residual_pnl", sa.Float(), nullable=True),
        sa.Column("anomaly_count_trailing_7d", sa.Integer(), nullable=False),
        sa.Column("anomaly_count_prev_7d", sa.Integer(), nullable=False),
        sa.Column("anomaly_trend", sa.String(length=16), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_health_records_run_id", "run_health_records", ["run_id"])
    op.create_index("ix_run_health_records_profile_id", "run_health_records", ["profile_id"])
    op.create_index("ix_run_health_records_mode", "run_health_records", ["mode"])
    op.create_index("ix_run_health_records_broker", "run_health_records", ["broker"])
    op.create_index("ix_run_health_records_account", "run_health_records", ["account"])
    op.create_index("ix_run_health_records_anomaly_trend", "run_health_records", ["anomaly_trend"])
    op.create_index("ix_run_health_records_created_at", "run_health_records", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_run_health_records_created_at", table_name="run_health_records")
    op.drop_index("ix_run_health_records_anomaly_trend", table_name="run_health_records")
    op.drop_index("ix_run_health_records_account", table_name="run_health_records")
    op.drop_index("ix_run_health_records_broker", table_name="run_health_records")
    op.drop_index("ix_run_health_records_mode", table_name="run_health_records")
    op.drop_index("ix_run_health_records_profile_id", table_name="run_health_records")
    op.drop_index("ix_run_health_records_run_id", table_name="run_health_records")
    op.drop_table("run_health_records")
