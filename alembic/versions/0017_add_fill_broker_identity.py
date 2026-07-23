"""Persist stable broker fill identity."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017_add_fill_broker_identity"
down_revision = "0016_add_order_broker_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加账户、execution 与合约身份，支持并发安全去重。"""

    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.add_column(sa.Column("broker", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("account", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("exec_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("perm_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("client_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("con_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_fill_records_broker", ["broker"], unique=False)
        batch_op.create_index("ix_fill_records_account", ["account"], unique=False)
        batch_op.create_index("ix_fill_records_exec_id", ["exec_id"], unique=False)
        batch_op.create_index("ix_fill_records_perm_id", ["perm_id"], unique=False)
        batch_op.create_index("ix_fill_records_con_id", ["con_id"], unique=False)
        batch_op.create_unique_constraint(
            "uq_fill_records_broker_account_exec_id",
            ["broker", "account", "exec_id"],
        )


def downgrade() -> None:
    """移除券商成交身份字段。"""

    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.drop_constraint(
            "uq_fill_records_broker_account_exec_id",
            type_="unique",
        )
        batch_op.drop_index("ix_fill_records_con_id")
        batch_op.drop_index("ix_fill_records_perm_id")
        batch_op.drop_index("ix_fill_records_exec_id")
        batch_op.drop_index("ix_fill_records_account")
        batch_op.drop_index("ix_fill_records_broker")
        batch_op.drop_column("con_id")
        batch_op.drop_column("client_id")
        batch_op.drop_column("perm_id")
        batch_op.drop_column("exec_id")
        batch_op.drop_column("account")
        batch_op.drop_column("broker")
