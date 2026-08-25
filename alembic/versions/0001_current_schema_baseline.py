"""创建 Northstar Quant 当前开发期数据库基线。

本仓库尚未保留需要逐版升级的数据库。此 migration 是完整、显式且可审计的
PostgreSQL schema 快照；旧 revision 不再受支持，不能通过自动化回滚、stamp 或
清库来转换。
"""

from __future__ import annotations

from alembic import op
import northstar_quant.foundation.db.types
import sqlalchemy as sa

revision = "0001_current_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None


# Extension helpers are deliberately kept in this single migration rather than
# importing ORM metadata. A migration must remain a stable schema snapshot even
# when future models change.
# BASELINE_EXTENSION_HELPERS_BEGIN
def _apply_daily_targets_runtime_risk() -> None:
    """增加目标快照幂等约束和实时风控表。"""

    op.create_unique_constraint(
        "uq_strategy_run_records_run_id",
        "strategy_run_records",
        ["run_id"],
    )
    op.create_table(
        "runtime_risk_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("can_submit", sa.Boolean(), nullable=False),
        sa.Column("blocking_failure_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column(
            "checked_at",
            northstar_quant.foundation.db.types.UTCDateTime(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            northstar_quant.foundation.db.types.UTCDateTime(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "account",
        "broker",
        "can_submit",
        "checked_at",
        "created_at",
        "profile_id",
    ):
        op.create_index(
            op.f(f"ix_runtime_risk_records_{column}"),
            "runtime_risk_records",
            [column],
            unique=False,
        )


def _apply_ctp_sim_execution_semantics() -> None:
    """扩展执行、订单、成交和持仓快照。"""

    for name, column_type in (
        ("instrument_id", sa.String(length=32)),
        ("exchange_id", sa.String(length=16)),
        ("ctp_offset", sa.String(length=24)),
        ("volume_multiple", sa.Integer()),
        ("margin_rate", sa.Float()),
        ("required_margin", sa.Float()),
    ):
        op.add_column(
            "execution_plan_records",
            sa.Column(name, column_type, nullable=True),
        )

    for name, column_type in (
        ("ctp_offset", sa.String(length=24)),
        ("volume_multiple", sa.Integer()),
        ("margin_rate", sa.Float()),
        ("required_margin", sa.Float()),
    ):
        op.add_column(
            "order_records",
            sa.Column(name, column_type, nullable=True),
        )

    op.add_column(
        "fill_records",
        sa.Column("ctp_offset", sa.String(length=24), nullable=True),
    )

    for name, column_type in (
        ("instrument_id", sa.String(length=32)),
        ("exchange_id", sa.String(length=16)),
        ("long_today_qty", sa.Float()),
        ("long_yesterday_qty", sa.Float()),
        ("short_today_qty", sa.Float()),
        ("short_yesterday_qty", sa.Float()),
    ):
        op.add_column(
            "position_snapshot_records",
            sa.Column(name, column_type, nullable=True),
        )


def _apply_position_risk_semantics() -> None:
    for name in (
        "long_frozen_qty",
        "short_frozen_qty",
        "long_closable_qty",
        "short_closable_qty",
        "margin",
        "realized_pnl",
        "unrealized_pnl",
    ):
        op.add_column(
            "position_snapshot_records",
            sa.Column(name, sa.Float(), nullable=True),
        )


def _apply_reconciliation_safety_state() -> None:
    op.create_table(
        "reconciliation_safety_state_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("profile_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("broker", sa.String(length=32), nullable=False, index=True),
        sa.Column("account", sa.String(length=64), nullable=True, index=True),
        sa.Column("state", sa.String(length=32), nullable=False, index=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("predecessor_hash", sa.String(length=64), nullable=True),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("recovery_approver_id", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.UniqueConstraint(
            "state_hash",
            name="uq_reconciliation_safety_state_records_state_hash",
        ),
    )


def _apply_ledger_settlement_adjustments() -> None:
    op.create_table(
        "settlement_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("settlement_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("settlement_date", sa.Date(), nullable=False, index=True),
        sa.Column("broker", sa.String(length=32), nullable=False, index=True),
        sa.Column("account", sa.String(length=64), nullable=False, index=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("account_snapshot_id", sa.Integer(), nullable=True, index=True),
        sa.Column("cash_balance", sa.Float(), nullable=True),
        sa.Column("margin", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("fee", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "broker",
            "account",
            "settlement_id",
            name="uq_settlement_records_broker_account_settlement_id",
        ),
    )
    op.create_table(
        "ledger_adjustment_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("adjustment_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("broker", sa.String(length=32), nullable=False, index=True),
        sa.Column("account", sa.String(length=64), nullable=False, index=True),
        sa.Column("profile_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("approver_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("adjustment_id", name="uq_ledger_adjustment_records_adjustment_id"),
    )


def _apply_provenance_consumption() -> None:
    op.create_table(
        "execution_provenance_consumption_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("preflight_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("receipt_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("plan_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("order_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("profile_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("broker", sa.String(length=32), nullable=False, index=True),
        sa.Column("account", sa.String(length=64), nullable=False, index=True),
        sa.Column("order_ref", sa.String(length=64), nullable=False, index=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "broker",
            "account",
            "plan_hash",
            "order_hash",
            name="uq_execution_provenance_consumption_plan_order",
        ),
    )


def _apply_portfolio_risk_approval() -> None:
    op.create_table(
        "portfolio_risk_approval_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("approval_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("profile_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("broker", sa.String(length=32), nullable=False, index=True),
        sa.Column("account", sa.String(length=64), nullable=False, index=True),
        sa.Column("review_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("portfolio_target_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("approved_target_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("composition_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "composition_evidence_hash",
            sa.String(length=64),
            nullable=False,
            index=True,
        ),
        sa.Column("authority_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("policy_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "reconciliation_state_hash",
            sa.String(length=64),
            nullable=False,
            index=True,
        ),
        sa.Column("binding_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("attestation_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("approver_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("verifier_id", sa.String(length=128), nullable=False, index=True),
        sa.Column(
            "verifier_receipt_hash",
            sa.String(length=64),
            nullable=False,
            index=True,
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "review_evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("record_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.UniqueConstraint(
            "approval_id",
            name="uq_portfolio_risk_approval_records_approval_id",
        ),
        sa.UniqueConstraint(
            "profile_id",
            "broker",
            "account",
            "binding_hash",
            name="uq_portfolio_risk_approval_records_scope_binding",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_portfolio_risk_approval_records_record_hash",
        ),
    )


def _apply_research_agent_run_audit() -> None:
    op.create_table(
        "research_agent_run_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("event_kind", sa.String(length=16), nullable=False, index=True),
        sa.Column("is_terminal", sa.Boolean(), nullable=False, index=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True, index=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True, index=True),
        sa.Column("trace_count", sa.Integer(), nullable=False),
        sa.Column("trace_root_hash", sa.String(length=64), nullable=True, index=True),
        sa.Column("trace_tail_hash", sa.String(length=64), nullable=True, index=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column(
            "predecessor_record_hash",
            sa.String(length=64),
            nullable=True,
            index=True,
        ),
        sa.Column("lifecycle", sa.String(length=32), nullable=False, index=True),
        sa.Column(
            "eligible_for_trading",
            sa.Boolean(),
            nullable=False,
            index=True,
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False, index=True),
        sa.UniqueConstraint(
            "run_id",
            "event_kind",
            name="uq_research_agent_audit_run_kind",
        ),
        sa.UniqueConstraint(
            "run_id",
            "is_terminal",
            name="uq_research_agent_audit_run_terminal",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_research_agent_audit_record_hash",
        ),
        sa.CheckConstraint(
            "event_kind IN ('ADMITTED', 'COMPLETED', 'FAILED')",
            name="ck_research_agent_audit_event_kind",
        ),
        sa.CheckConstraint(
            "lifecycle = 'RESEARCH_ONLY'",
            name="ck_research_agent_audit_research_only",
        ),
        sa.CheckConstraint(
            "eligible_for_trading = false",
            name="ck_research_agent_audit_non_tradable",
        ),
        sa.CheckConstraint(
            "trace_count >= 0",
            name="ck_research_agent_audit_trace_count",
        ),
        sa.CheckConstraint(
            "(event_kind = 'ADMITTED' AND is_terminal = false "
            "AND result_hash IS NULL AND failure_code IS NULL "
            "AND trace_count = 0 AND trace_root_hash IS NULL "
            "AND trace_tail_hash IS NULL AND predecessor_record_hash IS NULL) "
            "OR (event_kind = 'COMPLETED' AND is_terminal = true "
            "AND result_hash IS NOT NULL AND failure_code IS NULL "
            "AND trace_count > 0 AND trace_root_hash IS NOT NULL "
            "AND trace_tail_hash IS NOT NULL AND predecessor_record_hash IS NOT NULL) "
            "OR (event_kind = 'FAILED' AND is_terminal = true "
            "AND result_hash IS NULL AND failure_code IS NOT NULL "
            "AND trace_count = 0 AND trace_root_hash IS NULL "
            "AND trace_tail_hash IS NULL AND predecessor_record_hash IS NOT NULL)",
            name="ck_research_agent_audit_event_shape",
        ),
    )
    op.create_table(
        "research_agent_run_trace_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False, index=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("response_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "predecessor_trace_hash",
            sa.String(length=64),
            nullable=True,
            index=True,
        ),
        sa.Column("trace_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("lifecycle", sa.String(length=32), nullable=False, index=True),
        sa.Column(
            "eligible_for_trading",
            sa.Boolean(),
            nullable=False,
            index=True,
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False, index=True),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_research_agent_trace_run_sequence",
        ),
        sa.UniqueConstraint(
            "run_id",
            "trace_hash",
            name="uq_research_agent_trace_run_hash",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_research_agent_trace_record_hash",
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_research_agent_trace_positive_sequence",
        ),
        sa.CheckConstraint(
            "lifecycle = 'RESEARCH_ONLY'",
            name="ck_research_agent_trace_research_only",
        ),
        sa.CheckConstraint(
            "eligible_for_trading = false",
            name="ck_research_agent_trace_non_tradable",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION northstar_reject_research_agent_run_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'RESEARCH_AGENT_RUN_AUDIT_IMMUTABLE';
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_research_agent_audit_events_immutable
        BEFORE UPDATE OR DELETE ON research_agent_run_audit_events
        FOR EACH ROW
        EXECUTE FUNCTION northstar_reject_research_agent_run_audit_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_research_agent_trace_entries_immutable
        BEFORE UPDATE OR DELETE ON research_agent_run_trace_entries
        FOR EACH ROW
        EXECUTE FUNCTION northstar_reject_research_agent_run_audit_mutation();
        """
    )


_SHA256 = "^[0-9a-f]{64}$"
_TRACE_TOOL_NAMES = (
    "'search_events'",
    "'search_datasets'",
    "'get_feature'",
    "'create_experiment'",
    "'run_backtest'",
    "'run_validation'",
    "'generate_research_card'",
)
_FAILURE_CODES = ("'RESEARCH_AGENT_RESULT_INVALID'",)


def _apply_research_agent_run_audit_hardening() -> None:
    """Add non-destructive integrity checks and immutable TRUNCATE refusal."""

    for constraint_name, condition in (
        ("ck_research_agent_audit_request_hash", f"request_hash ~ '{_SHA256}'"),
        (
            "ck_research_agent_audit_result_hash",
            f"result_hash IS NULL OR result_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_audit_trace_root_hash",
            f"trace_root_hash IS NULL OR trace_root_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_audit_trace_tail_hash",
            f"trace_tail_hash IS NULL OR trace_tail_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_audit_predecessor_record_hash",
            f"predecessor_record_hash IS NULL OR predecessor_record_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_audit_record_hash_shape",
            f"record_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_audit_failure_code",
            f"failure_code IS NULL OR failure_code IN ({', '.join(_FAILURE_CODES)})",
        ),
    ):
        op.create_check_constraint(
            constraint_name,
            "research_agent_run_audit_events",
            condition,
        )

    for constraint_name, condition in (
        (
            "ck_research_agent_trace_tool_name",
            f"tool_name IN ({', '.join(_TRACE_TOOL_NAMES)})",
        ),
        (
            "ck_research_agent_trace_request_hash",
            f"request_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_trace_response_hash",
            f"response_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_trace_predecessor_hash",
            f"predecessor_trace_hash IS NULL OR predecessor_trace_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_trace_hash_shape",
            f"trace_hash ~ '{_SHA256}'",
        ),
        (
            "ck_research_agent_trace_record_hash_shape",
            f"record_hash ~ '{_SHA256}'",
        ),
    ):
        op.create_check_constraint(
            constraint_name,
            "research_agent_run_trace_entries",
            condition,
        )

    # ``Operations.execute`` also accepts a SQLAlchemy executable.  Keeping
    # the trigger definition as ``sa.text`` makes the migration-preservation
    # check distinguish declarative TRUNCATE refusal from destructive SQL.
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_research_agent_audit_events_reject_truncate
            BEFORE TRUNCATE ON research_agent_run_audit_events
            FOR EACH STATEMENT
            EXECUTE FUNCTION northstar_reject_research_agent_run_audit_mutation();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_research_agent_trace_entries_reject_truncate
            BEFORE TRUNCATE ON research_agent_run_trace_entries
            FOR EACH STATEMENT
            EXECUTE FUNCTION northstar_reject_research_agent_run_audit_mutation();
            """
        )
    )


# BASELINE_EXTENSION_HELPERS_END


def upgrade() -> None:
    """创建当前开发期 ORM schema、数据库约束和不可变审计触发器。"""
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
        sa.Column("start_asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
        sa.Column("end_asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("created_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("detected_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
        sa.Column("created_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("created_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column(
            "requested_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False
        ),
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
        sa.Column("acquired_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
        sa.Column(
            "heartbeat_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False
        ),
        sa.Column("expires_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("created_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("filled_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("prepared_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
        sa.Column(
            "submission_started_at",
            northstar_quant.foundation.db.types.UTCDateTime(),
            nullable=True,
        ),
        sa.Column("submitted_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=True),
        sa.Column(
            "broker_acknowledged_at",
            northstar_quant.foundation.db.types.UTCDateTime(),
            nullable=True,
        ),
        sa.Column("updated_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
        sa.Column("created_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("created_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("created_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column(
            "market_data_asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=True
        ),
        sa.Column(
            "signal_data_asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=True
        ),
        sa.Column("output_asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=True),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
        sa.Column("created_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column("asof", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
        sa.Column(
            "attributed_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False
        ),
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
        sa.Column("submitted_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=True),
        sa.Column("observed_at", northstar_quant.foundation.db.types.UTCDateTime(), nullable=False),
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
    _apply_daily_targets_runtime_risk()
    _apply_ctp_sim_execution_semantics()
    _apply_position_risk_semantics()
    _apply_reconciliation_safety_state()
    _apply_ledger_settlement_adjustments()
    _apply_provenance_consumption()
    _apply_portfolio_risk_approval()
    _apply_research_agent_run_audit()
    _apply_research_agent_run_audit_hardening()

    # ### end Alembic commands ###


def downgrade() -> None:
    """禁止回滚：仓库迁移只允许前向升级。"""
    raise RuntimeError(
        "数据库迁移只允许前向升级（forward-only）；不支持回滚或破坏性 schema 删除。"
        "如需删除或清空数据库，请由用户在仓库自动化之外手动执行。 "
        "Database migrations are forward-only; rollback and destructive schema removal "
        "are unsupported. Database deletion or clearing must be performed manually by "
        "the user outside repository automation."
    )
