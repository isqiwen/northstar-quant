"""PostgreSQL 测试数据库工厂。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from northstar_quant.foundation.db.base import Base
from tests.helpers.postgresql import postgresql_test_url


def create_postgresql_test_engine(key: str | Path) -> Engine:
    """创建已初始化模型表的隔离 PostgreSQL engine。"""

    engine = create_engine(postgresql_test_url(key), future=True)
    Base.metadata.create_all(bind=engine)
    return engine


def create_postgresql_session_factory(
    engine: Engine,
) -> sessionmaker[Session]:
    """创建绑定测试 engine 的统一 session factory。"""

    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
