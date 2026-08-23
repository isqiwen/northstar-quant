"""Add append-only P8 CTP-sim provenance-consumption facts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_provenance_consumption"
down_revision = "0006_ledger_settlement"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only; rollback is unsupported.")
