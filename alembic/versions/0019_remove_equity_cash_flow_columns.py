"""移除已废弃的现金流字段。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0019_remove_equity_cash_flow_columns"
down_revision = "0018_add_durable_submission_and_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除分红和公司行为现金流字段。"""

    with op.batch_alter_table("account_attribution_records") as batch_op:
        batch_op.drop_column("corporate_action_cash_flow")
        batch_op.drop_column("dividend_cash_flow")


def downgrade() -> None:
    """恢复旧字段，供迁移回滚使用。"""

    with op.batch_alter_table("account_attribution_records") as batch_op:
        batch_op.add_column(sa.Column("dividend_cash_flow", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("corporate_action_cash_flow", sa.Float(), nullable=True))
