"""Use timezone-aware datetimes for core audit tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_use_timezone_aware_datetimes"
down_revision = "0003_add_order_semantic_to_order_records"
branch_labels = None
depends_on = None


_DATETIME_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_logs", "created_at"),
    ("signal_records", "asof"),
    ("order_records", "submitted_at"),
    ("fill_records", "filled_at"),
    ("position_snapshot_records", "asof"),
    ("broker_sync_logs", "created_at"),
)


def _postgres_alter(*, existing_type: sa.DateTime, target_type: sa.DateTime) -> None:
    for table_name, column_name in _DATETIME_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=existing_type,
            type_=target_type,
            postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _postgres_alter(
        existing_type=sa.DateTime(),
        target_type=sa.DateTime(timezone=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _postgres_alter(
        existing_type=sa.DateTime(timezone=True),
        target_type=sa.DateTime(),
    )
