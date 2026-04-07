"""数据库会话管理。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from northstar_quant.config.settings import get_settings


def make_engine():
    """创建数据库引擎。"""

    settings = get_settings()
    return create_engine(settings.database_url, future=True)


SessionLocal = sessionmaker(bind=make_engine(), autoflush=False, autocommit=False, expire_on_commit=False, future=True)
