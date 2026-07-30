"""冻结日频目标并记录盘中实时风控结论。"""

from __future__ import annotations

from alembic import op
import northstar_quant.db.types
import sqlalchemy as sa

revision = "0002_daily_targets_runtime_risk"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加目标快照幂等约束和实时风控表。"""

    op.create_unique_constraint(
        "uq_strategy_run_records_run_id",
        "strategy_run_records",
        ["run_id"],
    )
    op.create_table(
        "runtime_risk_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=True),
        sa.Column("can_submit", sa.Boolean(), nullable=False),
        sa.Column("blocking_failure_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column(
            "checked_at",
            northstar_quant.db.types.UTCDateTime(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            northstar_quant.db.types.UTCDateTime(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "account",
        "broker",
        "can_submit",
        "checked_at",
        "created_at",
        "profile_id",
    ):
        op.create_index(
            op.f(f"ix_runtime_risk_records_{column}"),
            "runtime_risk_records",
            [column],
            unique=False,
        )


def downgrade() -> None:
    """删除实时风控表和目标快照幂等约束。"""

    for column in (
        "profile_id",
        "created_at",
        "checked_at",
        "can_submit",
        "broker",
        "account",
    ):
        op.drop_index(
            op.f(f"ix_runtime_risk_records_{column}"),
            table_name="runtime_risk_records",
        )
    op.drop_table("runtime_risk_records")
    op.drop_constraint(
        "uq_strategy_run_records_run_id",
        "strategy_run_records",
        type_="unique",
    )
