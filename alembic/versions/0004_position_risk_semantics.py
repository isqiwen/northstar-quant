"""Add non-destructive futures position risk fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_position_risk_semantics"
down_revision = "0003_ctp_sim_execution_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only; rollback is unsupported.")
