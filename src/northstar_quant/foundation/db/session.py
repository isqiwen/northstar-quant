"""数据库会话管理。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from northstar_quant.foundation.config.settings import get_settings


def make_engine():
    """创建 PostgreSQL 数据库引擎。"""

    settings = get_settings()
    return create_engine(
        settings.database_url,
        future=True,
        pool_pre_ping=True,
    )


_session_factory: sessionmaker[Session] | None = None


def SessionLocal() -> Session:
    """Create a core PostgreSQL session after runtime configuration is validated.

    Module import must not read a user's `.env`: tooling and test composition
    often import application types before selecting their isolated session
    factory.  The first actual core database operation still validates
    ``NORTHSTAR_DATABASE_URL`` and never has a SQLite fallback.
    """

    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=make_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _session_factory()
