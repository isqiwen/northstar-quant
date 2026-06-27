"""Add snapshot batch id to position snapshots."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_add_position_snapshot_batch_id"
down_revision = "0004_use_timezone_aware_datetimes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("position_snapshot_records") as batch_op:
        batch_op.add_column(sa.Column("snapshot_batch_id", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_position_snapshot_records_snapshot_batch_id",
        "position_snapshot_records",
        ["snapshot_batch_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_position_snapshot_records_snapshot_batch_id",
        table_name="position_snapshot_records",
    )
    with op.batch_alter_table("position_snapshot_records") as batch_op:
        batch_op.drop_column("snapshot_batch_id")
