"""Add durable order submission identity and cross-process leases."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0018_add_durable_submission_and_leases"
down_revision = "0017_add_fill_broker_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加订单提交状态机字段和执行租约表。"""

    with op.batch_alter_table("order_records") as batch_op:
        batch_op.add_column(
            sa.Column("broker_symbol", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(sa.Column("con_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("sec_type", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("exchange", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("primary_exchange", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("currency", sa.String(length=8), nullable=True)
        )
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=80), nullable=True)
        )
        batch_op.add_column(
            sa.Column("request_fingerprint", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "execution_policy_fingerprint",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "attempt_no",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(
            sa.Column("order_ref", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("client_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("perm_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("filled_qty", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("remaining_qty", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("avg_fill_price", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("submission_owner", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("lease_fencing_token", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_submission_error", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "prepared_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "submission_started_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.alter_column(
            "submitted_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "broker_acknowledged_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )
        batch_op.create_index(
            "ix_order_records_idempotency_key",
            ["idempotency_key"],
            unique=False,
        )
        batch_op.create_index(
            "ix_order_records_order_ref",
            ["order_ref"],
            unique=False,
        )
        batch_op.create_index(
            "ix_order_records_execution_policy_fingerprint",
            ["execution_policy_fingerprint"],
            unique=False,
        )
        batch_op.create_index(
            "ix_order_records_con_id",
            ["con_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_order_records_perm_id",
            ["perm_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_order_records_submission_owner",
            ["submission_owner"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_order_records_broker_account_idempotency_key",
            ["broker", "account", "idempotency_key"],
        )
        batch_op.create_unique_constraint(
            "uq_order_records_broker_account_order_ref",
            ["broker", "account", "order_ref"],
        )
        batch_op.create_unique_constraint(
            "uq_order_records_broker_account_perm_id",
            ["broker", "account", "perm_id"],
        )
        batch_op.create_unique_constraint(
            "uq_order_records_broker_account_client_order_id",
            ["broker", "account", "client_id", "broker_order_id"],
        )

    op.create_table(
        "execution_lease_records",
        sa.Column("resource_key", sa.String(length=255), nullable=False),
        sa.Column("owner_token", sa.String(length=64), nullable=False),
        sa.Column(
            "fencing_token",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("resource_key"),
    )
    op.create_index(
        "ix_execution_lease_records_owner_token",
        "execution_lease_records",
        ["owner_token"],
        unique=False,
    )
    op.create_index(
        "ix_execution_lease_records_expires_at",
        "execution_lease_records",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """移除执行租约和订单提交状态机字段。"""

    op.drop_index(
        "ix_execution_lease_records_expires_at",
        table_name="execution_lease_records",
    )
    op.drop_index(
        "ix_execution_lease_records_owner_token",
        table_name="execution_lease_records",
    )
    op.drop_table("execution_lease_records")

    op.execute(
        "UPDATE order_records "
        "SET submitted_at = COALESCE(submitted_at, prepared_at)"
    )
    with op.batch_alter_table("order_records") as batch_op:
        batch_op.alter_column(
            "submitted_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.drop_constraint(
            "uq_order_records_broker_account_idempotency_key",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_order_records_broker_account_order_ref",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_order_records_broker_account_perm_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_order_records_broker_account_client_order_id",
            type_="unique",
        )
        batch_op.drop_index("ix_order_records_submission_owner")
        batch_op.drop_index("ix_order_records_perm_id")
        batch_op.drop_index("ix_order_records_con_id")
        batch_op.drop_index(
            "ix_order_records_execution_policy_fingerprint"
        )
        batch_op.drop_index("ix_order_records_order_ref")
        batch_op.drop_index("ix_order_records_idempotency_key")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("broker_acknowledged_at")
        batch_op.drop_column("last_submission_error")
        batch_op.drop_column("submission_started_at")
        batch_op.drop_column("prepared_at")
        batch_op.drop_column("lease_fencing_token")
        batch_op.drop_column("submission_owner")
        batch_op.drop_column("avg_fill_price")
        batch_op.drop_column("remaining_qty")
        batch_op.drop_column("filled_qty")
        batch_op.drop_column("perm_id")
        batch_op.drop_column("client_id")
        batch_op.drop_column("order_ref")
        batch_op.drop_column("attempt_no")
        batch_op.drop_column("execution_policy_fingerprint")
        batch_op.drop_column("request_fingerprint")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("currency")
        batch_op.drop_column("primary_exchange")
        batch_op.drop_column("exchange")
        batch_op.drop_column("sec_type")
        batch_op.drop_column("con_id")
        batch_op.drop_column("broker_symbol")
