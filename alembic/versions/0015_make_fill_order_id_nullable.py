"""Allow broker fills without a matched local order."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015_make_fill_order_id_nullable"
down_revision = "0014_add_run_health_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """允许先落库尚未匹配到本地订单的券商成交。"""

    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.alter_column(
            "order_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    """恢复旧约束；执行前必须先处理 order_id 为空的历史成交。"""

    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.alter_column(
            "order_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
