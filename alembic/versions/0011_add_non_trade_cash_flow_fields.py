"""Add non-trade cash-flow attribution fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0011_add_non_trade_cash_flow_fields"
down_revision = "0010_add_account_attribution_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("account_snapshot_records") as batch_op:
        batch_op.add_column(sa.Column("account_values_json", sa.Text(), nullable=True))

    with op.batch_alter_table("account_attribution_records") as batch_op:
        batch_op.add_column(sa.Column("dividend_cash_flow", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("interest_cash_flow", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("fee_cash_flow", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("tax_cash_flow", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("other_non_trade_cash_flow", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("total_non_trade_cash_flow", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("account_attribution_records") as batch_op:
        batch_op.drop_column("total_non_trade_cash_flow")
        batch_op.drop_column("other_non_trade_cash_flow")
        batch_op.drop_column("tax_cash_flow")
        batch_op.drop_column("fee_cash_flow")
        batch_op.drop_column("interest_cash_flow")
        batch_op.drop_column("dividend_cash_flow")

    with op.batch_alter_table("account_snapshot_records") as batch_op:
        batch_op.drop_column("account_values_json")
