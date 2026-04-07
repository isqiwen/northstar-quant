"""Add order_semantic to order records."""

from alembic import op
import sqlalchemy as sa

revision = "0003_add_order_semantic_to_order_records"
down_revision = "0002_add_positions_and_broker_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("order_records") as batch_op:
        batch_op.add_column(sa.Column("order_semantic", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("order_records") as batch_op:
        batch_op.drop_column("order_semantic")
