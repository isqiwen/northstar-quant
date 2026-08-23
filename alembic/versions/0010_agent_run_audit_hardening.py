"""Harden immutable, hash-only ResearchAgent audit storage.

This forward-only migration adds database-enforced shape constraints and
statement-level TRUNCATE refusal to the append-only audit tables created by
``0009_agent_run_audit``.  It never rewrites, deletes, or clears evidence.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_agent_run_audit_hardening"
down_revision = "0009_agent_run_audit"
branch_labels = None
depends_on = None


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


def upgrade() -> None:
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
            "failure_code IS NULL OR failure_code IN ("
            f"{', '.join(_FAILURE_CODES)})",
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


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only; rollback is unsupported.")
