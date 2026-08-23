"""Add append-only reconciliation safety state audit records."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_reconciliation_safety_state"
down_revision = "0004_position_risk_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only; rollback is unsupported.")
