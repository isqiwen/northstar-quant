"""Add anomaly event records."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013_add_anomaly_event_records"
down_revision = "0012_add_funding_and_corporate_action_cash_flow_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anomaly_event_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_attribution_id", sa.Integer(), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("report_type", sa.String(length=16), nullable=False),
        sa.Column("alert_code", sa.String(length=64), nullable=False),
        sa.Column("alert_tag", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("report_path", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_anomaly_event_records_account_attribution_id", "anomaly_event_records", ["account_attribution_id"])
    op.create_index("ix_anomaly_event_records_profile_id", "anomaly_event_records", ["profile_id"])
    op.create_index("ix_anomaly_event_records_account", "anomaly_event_records", ["account"])
    op.create_index("ix_anomaly_event_records_run_id", "anomaly_event_records", ["run_id"])
    op.create_index("ix_anomaly_event_records_report_type", "anomaly_event_records", ["report_type"])
    op.create_index("ix_anomaly_event_records_alert_code", "anomaly_event_records", ["alert_code"])
    op.create_index("ix_anomaly_event_records_alert_tag", "anomaly_event_records", ["alert_tag"])
    op.create_index("ix_anomaly_event_records_severity", "anomaly_event_records", ["severity"])
    op.create_index("ix_anomaly_event_records_detected_at", "anomaly_event_records", ["detected_at"])


def downgrade() -> None:
    op.drop_index("ix_anomaly_event_records_detected_at", table_name="anomaly_event_records")
    op.drop_index("ix_anomaly_event_records_severity", table_name="anomaly_event_records")
    op.drop_index("ix_anomaly_event_records_alert_tag", table_name="anomaly_event_records")
    op.drop_index("ix_anomaly_event_records_alert_code", table_name="anomaly_event_records")
    op.drop_index("ix_anomaly_event_records_report_type", table_name="anomaly_event_records")
    op.drop_index("ix_anomaly_event_records_run_id", table_name="anomaly_event_records")
    op.drop_index("ix_anomaly_event_records_account", table_name="anomaly_event_records")
    op.drop_index("ix_anomaly_event_records_profile_id", table_name="anomaly_event_records")
    op.drop_index("ix_anomaly_event_records_account_attribution_id", table_name="anomaly_event_records")
    op.drop_table("anomaly_event_records")
