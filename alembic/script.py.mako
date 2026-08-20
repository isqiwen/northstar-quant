"""${message}"""

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """禁止回滚：仓库迁移只允许前向升级。"""
    raise RuntimeError(
        "数据库迁移只允许前向升级（forward-only）；不支持回滚或破坏性 schema 删除。"
        "如需删除或清空数据库，请由用户在仓库自动化之外手动执行。"
    )
