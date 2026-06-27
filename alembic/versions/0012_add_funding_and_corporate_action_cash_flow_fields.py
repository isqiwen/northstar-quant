"""Add funding and corporate-action cash-flow attribution fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_add_funding_and_corporate_action_cash_flow_fields"
down_revision = "0011_add_non_trade_cash_flow_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("account_attribution_records") as batch_op:
        batch_op.add_column(sa.Column("funding_cash_flow", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("corporate_action_cash_flow", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("account_attribution_records") as batch_op:
        batch_op.drop_column("corporate_action_cash_flow")
        batch_op.drop_column("funding_cash_flow")
