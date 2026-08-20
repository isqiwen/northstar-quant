"""记录 CTP 开平仓、保证金和今昨仓语义。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_ctp_sim_execution_semantics"
down_revision = "0002_daily_targets_runtime_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """扩展执行、订单、成交和持仓快照。"""

    for name, column_type in (
        ("instrument_id", sa.String(length=32)),
        ("exchange_id", sa.String(length=16)),
        ("ctp_offset", sa.String(length=24)),
        ("volume_multiple", sa.Integer()),
        ("margin_rate", sa.Float()),
        ("required_margin", sa.Float()),
    ):
        op.add_column(
            "execution_plan_records",
            sa.Column(name, column_type, nullable=True),
        )

    for name, column_type in (
        ("ctp_offset", sa.String(length=24)),
        ("volume_multiple", sa.Integer()),
        ("margin_rate", sa.Float()),
        ("required_margin", sa.Float()),
    ):
        op.add_column(
            "order_records",
            sa.Column(name, column_type, nullable=True),
        )

    op.add_column(
        "fill_records",
        sa.Column("ctp_offset", sa.String(length=24), nullable=True),
    )

    for name, column_type in (
        ("instrument_id", sa.String(length=32)),
        ("exchange_id", sa.String(length=16)),
        ("long_today_qty", sa.Float()),
        ("long_yesterday_qty", sa.Float()),
        ("short_today_qty", sa.Float()),
        ("short_yesterday_qty", sa.Float()),
    ):
        op.add_column(
            "position_snapshot_records",
            sa.Column(name, column_type, nullable=True),
        )


def downgrade() -> None:
    """禁止回滚：仓库迁移只允许前向升级。"""
    raise RuntimeError(
        "数据库迁移只允许前向升级（forward-only）；不支持回滚或破坏性 schema 删除。"
        "如需删除或清空数据库，请由用户在仓库自动化之外手动执行。 "
        "Database migrations are forward-only; rollback and destructive schema removal "
        "are unsupported. Database deletion or clearing must be performed manually by "
        "the user outside repository automation."
    )
