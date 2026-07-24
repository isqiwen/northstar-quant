"""以 CTP 合约身份替换已废弃的券商合约身份字段。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0020_replace_legacy_identity_with_ctp_identity"
down_revision = "0019_remove_equity_cash_flow_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """移除已废弃字段，新增 CTP instrument_id 与 exchange_id。"""

    with op.batch_alter_table("order_records") as batch_op:
        batch_op.drop_index("ix_order_records_con_id")
        batch_op.add_column(sa.Column("instrument_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("exchange_id", sa.String(length=16), nullable=True))
        batch_op.create_index("ix_order_records_instrument_id", ["instrument_id"], unique=False)
        batch_op.drop_column("broker_symbol")
        batch_op.drop_column("con_id")
        batch_op.drop_column("sec_type")
        batch_op.drop_column("exchange")
        batch_op.drop_column("primary_exchange")

    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.drop_index("ix_fill_records_con_id")
        batch_op.add_column(sa.Column("instrument_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("exchange_id", sa.String(length=16), nullable=True))
        batch_op.create_index("ix_fill_records_instrument_id", ["instrument_id"], unique=False)
        batch_op.drop_column("con_id")


def downgrade() -> None:
    """不支持回滚到已删除的券商身份模型。"""

    raise NotImplementedError("不支持回滚到已删除的券商身份模型。")
