"""创建 Northstar Quant 当前完整数据库结构。"""

from __future__ import annotations

from alembic import op
import northstar_quant.db.types
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建当前 ORM 定义的全部表、索引和唯一约束。"""
    op.create_table(
        "account_attribution_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("start_account_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("end_account_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("start_position_snapshot_batch_id", sa.String(length=64), nullable=True),
        sa.Column("end_position_snapshot_batch_id", sa.String(length=64), nullable=True),
        sa.Column("start_asof", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.Column("end_asof", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.Column("starting_equity", sa.Float(), nullable=True),
        sa.Column("ending_equity", sa.Float(), nullable=True),
        sa.Column("equity_change", sa.Float(), nullable=True),
        sa.Column("starting_cash", sa.Float(), nullable=True),
        sa.Column("ending_cash", sa.Float(), nullable=True),
        sa.Column("cash_change", sa.Float(), nullable=True),
        sa.Column("price_pnl", sa.Float(), nullable=True),
        sa.Column("rebalance_pnl", sa.Float(), nullable=True),
        sa.Column("execution_shortfall", sa.Float(), nullable=True),
        sa.Column("interest_cash_flow", sa.Float(), nullable=True),
        sa.Column("fee_cash_flow", sa.Float(), nullable=True),
        sa.Column("tax_cash_flow", sa.Float(), nullable=True),
        sa.Column("funding_cash_flow", sa.Float(), nullable=True),
        sa.Column("other_non_trade_cash_flow", sa.Float(), nullable=True),
        sa.Column("total_non_trade_cash_flow", sa.Float(), nullable=True),
        sa.Column("traded_notional", sa.Float(), nullable=True),
        sa.Column("fill_count", sa.Integer(), nullable=False),
        sa.Column("residual_pnl", sa.Float(), nullable=True),
        sa.Column("created_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_attribution_records_account"),
        "account_attribution_records",
        ["account"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_broker"),
        "account_attribution_records",
        ["broker"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_end_account_snapshot_id"),
        "account_attribution_records",
        ["end_account_snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_end_asof"),
        "account_attribution_records",
        ["end_asof"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_end_position_snapshot_batch_id"),
        "account_attribution_records",
        ["end_position_snapshot_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_profile_id"),
        "account_attribution_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_run_id"),
        "account_attribution_records",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_start_account_snapshot_id"),
        "account_attribution_records",
        ["start_account_snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_start_asof"),
        "account_attribution_records",
        ["start_asof"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_attribution_records_start_position_snapshot_batch_id"),
        "account_attribution_records",
        ["start_position_snapshot_batch_id"],
        unique=False,
    )
    op.create_table(
        "account_snapshot_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("position_snapshot_batch_id", sa.String(length=64), nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("cash_balance", sa.Float(), nullable=True),
        sa.Column("net_liquidation", sa.Float(), nullable=True),
        sa.Column("gross_position_value", sa.Float(), nullable=True),
        sa.Column("net_position_value", sa.Float(), nullable=True),
        sa.Column("available_funds", sa.Float(), nullable=True),
        sa.Column("gross_exposure", sa.Float(), nullable=True),
        sa.Column("net_exposure", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("account_values_json", sa.Text(), nullable=True),
        sa.Column("asof", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_snapshot_records_account"),
        "account_snapshot_records",
        ["account"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_snapshot_records_asof"), "account_snapshot_records", ["asof"], unique=False
    )
    op.create_index(
        op.f("ix_account_snapshot_records_broker"),
        "account_snapshot_records",
        ["broker"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_snapshot_records_position_snapshot_batch_id"),
        "account_snapshot_records",
        ["position_snapshot_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_snapshot_records_profile_id"),
        "account_snapshot_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_snapshot_records_run_id"),
        "account_snapshot_records",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "anomaly_event_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
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
        sa.Column("detected_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.Column("created_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_anomaly_event_records_account"), "anomaly_event_records", ["account"], unique=False
    )
    op.create_index(
        op.f("ix_anomaly_event_records_account_attribution_id"),
        "anomaly_event_records",
        ["account_attribution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_anomaly_event_records_alert_code"),
        "anomaly_event_records",
        ["alert_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_anomaly_event_records_alert_tag"),
        "anomaly_event_records",
        ["alert_tag"],
        unique=False,
    )
    op.create_index(
        op.f("ix_anomaly_event_records_detected_at"),
        "anomaly_event_records",
        ["detected_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_anomaly_event_records_profile_id"),
        "anomaly_event_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_anomaly_event_records_report_type"),
        "anomaly_event_records",
        ["report_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_anomaly_event_records_run_id"), "anomaly_event_records", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_anomaly_event_records_severity"),
        "anomaly_event_records",
        ["severity"],
        unique=False,
    )
    op.create_table(
        "broker_sync_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("sync_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_broker_sync_logs_broker"), "broker_sync_logs", ["broker"], unique=False
    )
    op.create_index(
        op.f("ix_broker_sync_logs_status"), "broker_sync_logs", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_broker_sync_logs_sync_type"), "broker_sync_logs", ["sync_type"], unique=False
    )
    op.create_table(
        "cancel_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cancel_batch_id", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cancel_records_account"), "cancel_records", ["account"], unique=False)
    op.create_index(op.f("ix_cancel_records_broker"), "cancel_records", ["broker"], unique=False)
    op.create_index(
        op.f("ix_cancel_records_broker_order_id"),
        "cancel_records",
        ["broker_order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cancel_records_cancel_batch_id"),
        "cancel_records",
        ["cancel_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cancel_records_order_id"), "cancel_records", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_cancel_records_profile_id"), "cancel_records", ["profile_id"], unique=False
    )
    op.create_index(
        op.f("ix_cancel_records_requested_at"), "cancel_records", ["requested_at"], unique=False
    )
    op.create_index(op.f("ix_cancel_records_run_id"), "cancel_records", ["run_id"], unique=False)
    op.create_index(op.f("ix_cancel_records_status"), "cancel_records", ["status"], unique=False)
    op.create_table(
        "execution_lease_records",
        sa.Column("resource_key", sa.String(length=255), nullable=False),
        sa.Column("owner_token", sa.String(length=64), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("acquired_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.Column("heartbeat_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.Column("expires_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("resource_key"),
    )
    op.create_index(
        op.f("ix_execution_lease_records_expires_at"),
        "execution_lease_records",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_lease_records_owner_token"),
        "execution_lease_records",
        ["owner_token"],
        unique=False,
    )
    op.create_table(
        "execution_plan_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
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
        sa.Column("created_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_execution_plan_records_batch_id"),
        "execution_plan_records",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_plan_records_execution_planner_id"),
        "execution_plan_records",
        ["execution_planner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_plan_records_plan_id"),
        "execution_plan_records",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_plan_records_profile_id"),
        "execution_plan_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_plan_records_run_id"), "execution_plan_records", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_execution_plan_records_strategy_id"),
        "execution_plan_records",
        ["strategy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_plan_records_symbol"), "execution_plan_records", ["symbol"], unique=False
    )
    op.create_table(
        "fill_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=True),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("exec_id", sa.String(length=128), nullable=True),
        sa.Column("perm_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("instrument_id", sa.String(length=32), nullable=True),
        sa.Column("exchange_id", sa.String(length=16), nullable=True),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("filled_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "broker", "account", "exec_id", name="uq_fill_records_broker_account_exec_id"
        ),
    )
    op.create_index(op.f("ix_fill_records_account"), "fill_records", ["account"], unique=False)
    op.create_index(op.f("ix_fill_records_broker"), "fill_records", ["broker"], unique=False)
    op.create_index(
        op.f("ix_fill_records_broker_order_id"), "fill_records", ["broker_order_id"], unique=False
    )
    op.create_index(op.f("ix_fill_records_exec_id"), "fill_records", ["exec_id"], unique=False)
    op.create_index(
        op.f("ix_fill_records_instrument_id"), "fill_records", ["instrument_id"], unique=False
    )
    op.create_index(op.f("ix_fill_records_order_id"), "fill_records", ["order_id"], unique=False)
    op.create_index(op.f("ix_fill_records_perm_id"), "fill_records", ["perm_id"], unique=False)
    op.create_index(op.f("ix_fill_records_symbol"), "fill_records", ["symbol"], unique=False)
    op.create_table(
        "order_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=True),
        sa.Column("order_type", sa.String(length=16), nullable=True),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("order_semantic", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=True),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("instrument_id", sa.String(length=32), nullable=True),
        sa.Column("exchange_id", sa.String(length=16), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("reference_price", sa.Float(), nullable=True),
        sa.Column("reference_price_source", sa.String(length=32), nullable=True),
        sa.Column("planned_trade_value", sa.Float(), nullable=True),
        sa.Column("execution_planner_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("execution_policy_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("order_ref", sa.String(length=64), nullable=True),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("perm_id", sa.Integer(), nullable=True),
        sa.Column("filled_qty", sa.Float(), nullable=True),
        sa.Column("remaining_qty", sa.Float(), nullable=True),
        sa.Column("avg_fill_price", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submission_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_fencing_token", sa.Integer(), nullable=True),
        sa.Column("last_submission_error", sa.Text(), nullable=True),
        sa.Column("prepared_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.Column("submission_started_at", northstar_quant.db.types.UTCDateTime(), nullable=True),
        sa.Column("submitted_at", northstar_quant.db.types.UTCDateTime(), nullable=True),
        sa.Column("broker_acknowledged_at", northstar_quant.db.types.UTCDateTime(), nullable=True),
        sa.Column("updated_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "broker",
            "account",
            "client_id",
            "broker_order_id",
            name="uq_order_records_broker_account_client_order_id",
        ),
        sa.UniqueConstraint(
            "broker",
            "account",
            "idempotency_key",
            name="uq_order_records_broker_account_idempotency_key",
        ),
        sa.UniqueConstraint(
            "broker", "account", "order_ref", name="uq_order_records_broker_account_order_ref"
        ),
        sa.UniqueConstraint(
            "broker", "account", "perm_id", name="uq_order_records_broker_account_perm_id"
        ),
    )
    op.create_index(op.f("ix_order_records_account"), "order_records", ["account"], unique=False)
    op.create_index(op.f("ix_order_records_batch_id"), "order_records", ["batch_id"], unique=False)
    op.create_index(op.f("ix_order_records_broker"), "order_records", ["broker"], unique=False)
    op.create_index(
        op.f("ix_order_records_execution_policy_fingerprint"),
        "order_records",
        ["execution_policy_fingerprint"],
        unique=False,
    )
    op.create_index(
        op.f("ix_order_records_idempotency_key"), "order_records", ["idempotency_key"], unique=False
    )
    op.create_index(
        op.f("ix_order_records_instrument_id"), "order_records", ["instrument_id"], unique=False
    )
    op.create_index(
        op.f("ix_order_records_order_ref"), "order_records", ["order_ref"], unique=False
    )
    op.create_index(op.f("ix_order_records_perm_id"), "order_records", ["perm_id"], unique=False)
    op.create_index(op.f("ix_order_records_plan_id"), "order_records", ["plan_id"], unique=False)
    op.create_index(
        op.f("ix_order_records_profile_id"), "order_records", ["profile_id"], unique=False
    )
    op.create_index(op.f("ix_order_records_run_id"), "order_records", ["run_id"], unique=False)
    op.create_index(op.f("ix_order_records_status"), "order_records", ["status"], unique=False)
    op.create_index(
        op.f("ix_order_records_strategy_id"), "order_records", ["strategy_id"], unique=False
    )
    op.create_index(
        op.f("ix_order_records_submission_owner"),
        "order_records",
        ["submission_owner"],
        unique=False,
    )
    op.create_index(op.f("ix_order_records_symbol"), "order_records", ["symbol"], unique=False)
    op.create_table(
        "position_snapshot_batch_records",
        sa.Column("snapshot_batch_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("broker", sa.String(length=32), nullable=True),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("asof", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.Column("created_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_batch_id"),
    )
    op.create_index(
        op.f("ix_position_snapshot_batch_records_account"),
        "position_snapshot_batch_records",
        ["account"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_snapshot_batch_records_asof"),
        "position_snapshot_batch_records",
        ["asof"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_snapshot_batch_records_broker"),
        "position_snapshot_batch_records",
        ["broker"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_snapshot_batch_records_profile_id"),
        "position_snapshot_batch_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_snapshot_batch_records_run_id"),
        "position_snapshot_batch_records",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "position_snapshot_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("avg_cost", sa.Float(), nullable=True),
        sa.Column("market_price", sa.Float(), nullable=True),
        sa.Column("market_value", sa.Float(), nullable=True),
        sa.Column("asof", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.Column("snapshot_batch_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_position_snapshot_records_account"),
        "position_snapshot_records",
        ["account"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_snapshot_records_asof"),
        "position_snapshot_records",
        ["asof"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_snapshot_records_snapshot_batch_id"),
        "position_snapshot_records",
        ["snapshot_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_snapshot_records_symbol"),
        "position_snapshot_records",
        ["symbol"],
        unique=False,
    )
    op.create_table(
        "run_health_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
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
        sa.Column("created_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_run_health_records_account"), "run_health_records", ["account"], unique=False
    )
    op.create_index(
        op.f("ix_run_health_records_anomaly_trend"),
        "run_health_records",
        ["anomaly_trend"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_health_records_broker"), "run_health_records", ["broker"], unique=False
    )
    op.create_index(
        op.f("ix_run_health_records_created_at"), "run_health_records", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_run_health_records_mode"), "run_health_records", ["mode"], unique=False
    )
    op.create_index(
        op.f("ix_run_health_records_profile_id"), "run_health_records", ["profile_id"], unique=False
    )
    op.create_index(
        op.f("ix_run_health_records_run_id"), "run_health_records", ["run_id"], unique=False
    )
    op.create_table(
        "run_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_run_logs_status"), "run_logs", ["status"], unique=False)
    op.create_index(op.f("ix_run_logs_task_name"), "run_logs", ["task_name"], unique=False)
    op.create_table(
        "signal_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("signal_value", sa.Float(), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=False),
        sa.Column("asof", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_signal_records_asof"), "signal_records", ["asof"], unique=False)
    op.create_index(
        op.f("ix_signal_records_strategy_id"), "signal_records", ["strategy_id"], unique=False
    )
    op.create_index(op.f("ix_signal_records_symbol"), "signal_records", ["symbol"], unique=False)
    op.create_table(
        "strategy_run_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("pipeline_strategy_id", sa.String(length=128), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("selected_strategy_ids_json", sa.Text(), nullable=True),
        sa.Column("strategy_params_json", sa.Text(), nullable=True),
        sa.Column("risk_limits_json", sa.Text(), nullable=True),
        sa.Column("market_data_asof", northstar_quant.db.types.UTCDateTime(), nullable=True),
        sa.Column("signal_data_asof", northstar_quant.db.types.UTCDateTime(), nullable=True),
        sa.Column("output_asof", northstar_quant.db.types.UTCDateTime(), nullable=True),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
        sa.Column("created_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_strategy_run_records_output_asof"),
        "strategy_run_records",
        ["output_asof"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_run_records_output_type"),
        "strategy_run_records",
        ["output_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_run_records_pipeline_strategy_id"),
        "strategy_run_records",
        ["pipeline_strategy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_run_records_profile_id"),
        "strategy_run_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_run_records_run_id"), "strategy_run_records", ["run_id"], unique=False
    )
    op.create_table(
        "strategy_snapshot_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
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
        sa.Column("asof", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_strategy_snapshot_records_asof"),
        "strategy_snapshot_records",
        ["asof"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_snapshot_records_output_type"),
        "strategy_snapshot_records",
        ["output_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_snapshot_records_pipeline_strategy_id"),
        "strategy_snapshot_records",
        ["pipeline_strategy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_snapshot_records_profile_id"),
        "strategy_snapshot_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_snapshot_records_run_id"),
        "strategy_snapshot_records",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_snapshot_records_source_strategy_id"),
        "strategy_snapshot_records",
        ["source_strategy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_snapshot_records_symbol"),
        "strategy_snapshot_records",
        ["symbol"],
        unique=False,
    )
    op.create_table(
        "trade_attribution_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fill_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True),
        sa.Column("account", sa.String(length=64), nullable=True),
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
        sa.Column("attributed_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_trade_attribution_records_account"),
        "trade_attribution_records",
        ["account"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_attributed_at"),
        "trade_attribution_records",
        ["attributed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_batch_id"),
        "trade_attribution_records",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_broker_order_id"),
        "trade_attribution_records",
        ["broker_order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_execution_planner_id"),
        "trade_attribution_records",
        ["execution_planner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_fill_id"),
        "trade_attribution_records",
        ["fill_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_order_id"),
        "trade_attribution_records",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_plan_id"),
        "trade_attribution_records",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_profile_id"),
        "trade_attribution_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_run_id"),
        "trade_attribution_records",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_strategy_id"),
        "trade_attribution_records",
        ["strategy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trade_attribution_records_symbol"),
        "trade_attribution_records",
        ["symbol"],
        unique=False,
    )
    op.create_table(
        "working_order_snapshot_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
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
        sa.Column("submitted_at", northstar_quant.db.types.UTCDateTime(), nullable=True),
        sa.Column("observed_at", northstar_quant.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_account"),
        "working_order_snapshot_records",
        ["account"],
        unique=False,
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_broker"),
        "working_order_snapshot_records",
        ["broker"],
        unique=False,
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_broker_order_id"),
        "working_order_snapshot_records",
        ["broker_order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_observed_at"),
        "working_order_snapshot_records",
        ["observed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_open_order_snapshot_batch_id"),
        "working_order_snapshot_records",
        ["open_order_snapshot_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_profile_id"),
        "working_order_snapshot_records",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_run_id"),
        "working_order_snapshot_records",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_status"),
        "working_order_snapshot_records",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_working_order_snapshot_records_symbol"),
        "working_order_snapshot_records",
        ["symbol"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """删除初始迁移创建的全部业务表。"""
    op.drop_index(
        op.f("ix_working_order_snapshot_records_symbol"),
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        op.f("ix_working_order_snapshot_records_status"),
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        op.f("ix_working_order_snapshot_records_run_id"),
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        op.f("ix_working_order_snapshot_records_profile_id"),
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        op.f("ix_working_order_snapshot_records_open_order_snapshot_batch_id"),
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        op.f("ix_working_order_snapshot_records_observed_at"),
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        op.f("ix_working_order_snapshot_records_broker_order_id"),
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        op.f("ix_working_order_snapshot_records_broker"),
        table_name="working_order_snapshot_records",
    )
    op.drop_index(
        op.f("ix_working_order_snapshot_records_account"),
        table_name="working_order_snapshot_records",
    )
    op.drop_table("working_order_snapshot_records")
    op.drop_index(
        op.f("ix_trade_attribution_records_symbol"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_strategy_id"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_run_id"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_profile_id"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_plan_id"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_order_id"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_fill_id"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_execution_planner_id"),
        table_name="trade_attribution_records",
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_broker_order_id"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_batch_id"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_attributed_at"), table_name="trade_attribution_records"
    )
    op.drop_index(
        op.f("ix_trade_attribution_records_account"), table_name="trade_attribution_records"
    )
    op.drop_table("trade_attribution_records")
    op.drop_index(
        op.f("ix_strategy_snapshot_records_symbol"), table_name="strategy_snapshot_records"
    )
    op.drop_index(
        op.f("ix_strategy_snapshot_records_source_strategy_id"),
        table_name="strategy_snapshot_records",
    )
    op.drop_index(
        op.f("ix_strategy_snapshot_records_run_id"), table_name="strategy_snapshot_records"
    )
    op.drop_index(
        op.f("ix_strategy_snapshot_records_profile_id"), table_name="strategy_snapshot_records"
    )
    op.drop_index(
        op.f("ix_strategy_snapshot_records_pipeline_strategy_id"),
        table_name="strategy_snapshot_records",
    )
    op.drop_index(
        op.f("ix_strategy_snapshot_records_output_type"), table_name="strategy_snapshot_records"
    )
    op.drop_index(op.f("ix_strategy_snapshot_records_asof"), table_name="strategy_snapshot_records")
    op.drop_table("strategy_snapshot_records")
    op.drop_index(op.f("ix_strategy_run_records_run_id"), table_name="strategy_run_records")
    op.drop_index(op.f("ix_strategy_run_records_profile_id"), table_name="strategy_run_records")
    op.drop_index(
        op.f("ix_strategy_run_records_pipeline_strategy_id"), table_name="strategy_run_records"
    )
    op.drop_index(op.f("ix_strategy_run_records_output_type"), table_name="strategy_run_records")
    op.drop_index(op.f("ix_strategy_run_records_output_asof"), table_name="strategy_run_records")
    op.drop_table("strategy_run_records")
    op.drop_index(op.f("ix_signal_records_symbol"), table_name="signal_records")
    op.drop_index(op.f("ix_signal_records_strategy_id"), table_name="signal_records")
    op.drop_index(op.f("ix_signal_records_asof"), table_name="signal_records")
    op.drop_table("signal_records")
    op.drop_index(op.f("ix_run_logs_task_name"), table_name="run_logs")
    op.drop_index(op.f("ix_run_logs_status"), table_name="run_logs")
    op.drop_table("run_logs")
    op.drop_index(op.f("ix_run_health_records_run_id"), table_name="run_health_records")
    op.drop_index(op.f("ix_run_health_records_profile_id"), table_name="run_health_records")
    op.drop_index(op.f("ix_run_health_records_mode"), table_name="run_health_records")
    op.drop_index(op.f("ix_run_health_records_created_at"), table_name="run_health_records")
    op.drop_index(op.f("ix_run_health_records_broker"), table_name="run_health_records")
    op.drop_index(op.f("ix_run_health_records_anomaly_trend"), table_name="run_health_records")
    op.drop_index(op.f("ix_run_health_records_account"), table_name="run_health_records")
    op.drop_table("run_health_records")
    op.drop_index(
        op.f("ix_position_snapshot_records_symbol"), table_name="position_snapshot_records"
    )
    op.drop_index(
        op.f("ix_position_snapshot_records_snapshot_batch_id"),
        table_name="position_snapshot_records",
    )
    op.drop_index(op.f("ix_position_snapshot_records_asof"), table_name="position_snapshot_records")
    op.drop_index(
        op.f("ix_position_snapshot_records_account"), table_name="position_snapshot_records"
    )
    op.drop_table("position_snapshot_records")
    op.drop_index(
        op.f("ix_position_snapshot_batch_records_run_id"),
        table_name="position_snapshot_batch_records",
    )
    op.drop_index(
        op.f("ix_position_snapshot_batch_records_profile_id"),
        table_name="position_snapshot_batch_records",
    )
    op.drop_index(
        op.f("ix_position_snapshot_batch_records_broker"),
        table_name="position_snapshot_batch_records",
    )
    op.drop_index(
        op.f("ix_position_snapshot_batch_records_asof"),
        table_name="position_snapshot_batch_records",
    )
    op.drop_index(
        op.f("ix_position_snapshot_batch_records_account"),
        table_name="position_snapshot_batch_records",
    )
    op.drop_table("position_snapshot_batch_records")
    op.drop_index(op.f("ix_order_records_symbol"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_submission_owner"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_strategy_id"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_status"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_run_id"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_profile_id"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_plan_id"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_perm_id"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_order_ref"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_instrument_id"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_idempotency_key"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_execution_policy_fingerprint"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_broker"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_batch_id"), table_name="order_records")
    op.drop_index(op.f("ix_order_records_account"), table_name="order_records")
    op.drop_table("order_records")
    op.drop_index(op.f("ix_fill_records_symbol"), table_name="fill_records")
    op.drop_index(op.f("ix_fill_records_perm_id"), table_name="fill_records")
    op.drop_index(op.f("ix_fill_records_order_id"), table_name="fill_records")
    op.drop_index(op.f("ix_fill_records_instrument_id"), table_name="fill_records")
    op.drop_index(op.f("ix_fill_records_exec_id"), table_name="fill_records")
    op.drop_index(op.f("ix_fill_records_broker_order_id"), table_name="fill_records")
    op.drop_index(op.f("ix_fill_records_broker"), table_name="fill_records")
    op.drop_index(op.f("ix_fill_records_account"), table_name="fill_records")
    op.drop_table("fill_records")
    op.drop_index(op.f("ix_execution_plan_records_symbol"), table_name="execution_plan_records")
    op.drop_index(
        op.f("ix_execution_plan_records_strategy_id"), table_name="execution_plan_records"
    )
    op.drop_index(op.f("ix_execution_plan_records_run_id"), table_name="execution_plan_records")
    op.drop_index(op.f("ix_execution_plan_records_profile_id"), table_name="execution_plan_records")
    op.drop_index(op.f("ix_execution_plan_records_plan_id"), table_name="execution_plan_records")
    op.drop_index(
        op.f("ix_execution_plan_records_execution_planner_id"), table_name="execution_plan_records"
    )
    op.drop_index(op.f("ix_execution_plan_records_batch_id"), table_name="execution_plan_records")
    op.drop_table("execution_plan_records")
    op.drop_index(
        op.f("ix_execution_lease_records_owner_token"), table_name="execution_lease_records"
    )
    op.drop_index(
        op.f("ix_execution_lease_records_expires_at"), table_name="execution_lease_records"
    )
    op.drop_table("execution_lease_records")
    op.drop_index(op.f("ix_cancel_records_status"), table_name="cancel_records")
    op.drop_index(op.f("ix_cancel_records_run_id"), table_name="cancel_records")
    op.drop_index(op.f("ix_cancel_records_requested_at"), table_name="cancel_records")
    op.drop_index(op.f("ix_cancel_records_profile_id"), table_name="cancel_records")
    op.drop_index(op.f("ix_cancel_records_order_id"), table_name="cancel_records")
    op.drop_index(op.f("ix_cancel_records_cancel_batch_id"), table_name="cancel_records")
    op.drop_index(op.f("ix_cancel_records_broker_order_id"), table_name="cancel_records")
    op.drop_index(op.f("ix_cancel_records_broker"), table_name="cancel_records")
    op.drop_index(op.f("ix_cancel_records_account"), table_name="cancel_records")
    op.drop_table("cancel_records")
    op.drop_index(op.f("ix_broker_sync_logs_sync_type"), table_name="broker_sync_logs")
    op.drop_index(op.f("ix_broker_sync_logs_status"), table_name="broker_sync_logs")
    op.drop_index(op.f("ix_broker_sync_logs_broker"), table_name="broker_sync_logs")
    op.drop_table("broker_sync_logs")
    op.drop_index(op.f("ix_anomaly_event_records_severity"), table_name="anomaly_event_records")
    op.drop_index(op.f("ix_anomaly_event_records_run_id"), table_name="anomaly_event_records")
    op.drop_index(op.f("ix_anomaly_event_records_report_type"), table_name="anomaly_event_records")
    op.drop_index(op.f("ix_anomaly_event_records_profile_id"), table_name="anomaly_event_records")
    op.drop_index(op.f("ix_anomaly_event_records_detected_at"), table_name="anomaly_event_records")
    op.drop_index(op.f("ix_anomaly_event_records_alert_tag"), table_name="anomaly_event_records")
    op.drop_index(op.f("ix_anomaly_event_records_alert_code"), table_name="anomaly_event_records")
    op.drop_index(
        op.f("ix_anomaly_event_records_account_attribution_id"), table_name="anomaly_event_records"
    )
    op.drop_index(op.f("ix_anomaly_event_records_account"), table_name="anomaly_event_records")
    op.drop_table("anomaly_event_records")
    op.drop_index(op.f("ix_account_snapshot_records_run_id"), table_name="account_snapshot_records")
    op.drop_index(
        op.f("ix_account_snapshot_records_profile_id"), table_name="account_snapshot_records"
    )
    op.drop_index(
        op.f("ix_account_snapshot_records_position_snapshot_batch_id"),
        table_name="account_snapshot_records",
    )
    op.drop_index(op.f("ix_account_snapshot_records_broker"), table_name="account_snapshot_records")
    op.drop_index(op.f("ix_account_snapshot_records_asof"), table_name="account_snapshot_records")
    op.drop_index(
        op.f("ix_account_snapshot_records_account"), table_name="account_snapshot_records"
    )
    op.drop_table("account_snapshot_records")
    op.drop_index(
        op.f("ix_account_attribution_records_start_position_snapshot_batch_id"),
        table_name="account_attribution_records",
    )
    op.drop_index(
        op.f("ix_account_attribution_records_start_asof"), table_name="account_attribution_records"
    )
    op.drop_index(
        op.f("ix_account_attribution_records_start_account_snapshot_id"),
        table_name="account_attribution_records",
    )
    op.drop_index(
        op.f("ix_account_attribution_records_run_id"), table_name="account_attribution_records"
    )
    op.drop_index(
        op.f("ix_account_attribution_records_profile_id"), table_name="account_attribution_records"
    )
    op.drop_index(
        op.f("ix_account_attribution_records_end_position_snapshot_batch_id"),
        table_name="account_attribution_records",
    )
    op.drop_index(
        op.f("ix_account_attribution_records_end_asof"), table_name="account_attribution_records"
    )
    op.drop_index(
        op.f("ix_account_attribution_records_end_account_snapshot_id"),
        table_name="account_attribution_records",
    )
    op.drop_index(
        op.f("ix_account_attribution_records_broker"), table_name="account_attribution_records"
    )
    op.drop_index(
        op.f("ix_account_attribution_records_account"), table_name="account_attribution_records"
    )
    op.drop_table("account_attribution_records")
    # ### end Alembic commands ###
