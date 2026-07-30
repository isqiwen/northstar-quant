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
    """删除 CTP 仿真执行语义字段。"""

    for name in (
        "short_yesterday_qty",
        "short_today_qty",
        "long_yesterday_qty",
        "long_today_qty",
        "exchange_id",
        "instrument_id",
    ):
        op.drop_column("position_snapshot_records", name)
    op.drop_column("fill_records", "ctp_offset")
    for name in (
        "required_margin",
        "margin_rate",
        "volume_multiple",
        "ctp_offset",
    ):
        op.drop_column("order_records", name)
    for name in (
        "required_margin",
        "margin_rate",
        "volume_multiple",
        "ctp_offset",
        "exchange_id",
        "instrument_id",
    ):
        op.drop_column("execution_plan_records", name)
