"""Add verifier-backed, append-only portfolio-risk approval records."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_portfolio_risk_approval"
down_revision = "0007_provenance_consumption"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only; rollback is unsupported.")
