"""数据库会话管理。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from northstar_quant.foundation.config.settings import get_settings


def make_engine():
    """创建 PostgreSQL 数据库引擎。"""

    settings = get_settings()
    return create_engine(
        settings.database_url,
        future=True,
        pool_pre_ping=True,
    )


SessionLocal = sessionmaker(bind=make_engine(), autoflush=False, autocommit=False, expire_on_commit=False, future=True)
