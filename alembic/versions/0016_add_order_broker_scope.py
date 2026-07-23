"""Add broker scope to persisted orders."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0016_add_order_broker_scope"
down_revision = "0015_make_fill_order_id_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为订单补充券商维度，支持账户级安全查询。"""

    with op.batch_alter_table("order_records") as batch_op:
        batch_op.add_column(
            sa.Column("broker", sa.String(length=32), nullable=True)
        )
        batch_op.create_index(
            "ix_order_records_broker",
            ["broker"],
            unique=False,
        )


def downgrade() -> None:
    """移除订单券商维度。"""

    with op.batch_alter_table("order_records") as batch_op:
        batch_op.drop_index("ix_order_records_broker")
        batch_op.drop_column("broker")
