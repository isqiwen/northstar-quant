"""Add append-only settlement and controlled-ledger-adjustment facts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_ledger_settlement"
down_revision = "0005_reconciliation_safety_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
            "broker", "account", "settlement_id",
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
        sa.UniqueConstraint(
            "adjustment_id", name="uq_ledger_adjustment_records_adjustment_id"
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only; rollback is unsupported.")
