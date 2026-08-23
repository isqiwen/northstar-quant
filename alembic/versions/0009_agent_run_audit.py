"""Add immutable, hash-only durable ResearchAgent run audit records."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_agent_run_audit"
down_revision = "0008_portfolio_risk_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only; rollback is unsupported.")
