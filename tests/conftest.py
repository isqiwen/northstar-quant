"""Real PostgreSQL lifecycle for data behavior tests."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from northstar_quant.db import initialize_database


def _reset(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session")
def postgres_engine() -> Generator[Engine, None, None]:
    database_url = os.environ.get("NORTHSTAR_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("NORTHSTAR_TEST_DATABASE_URL is required for PostgreSQL integration")
    parsed = make_url(database_url)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.database != "northstar_quant_test"
        or parsed.query
    ):
        raise pytest.UsageError(
            "use disposable postgresql+psycopg northstar_quant_test without URL options"
        )
    engine = create_engine(database_url, pool_pre_ping=True)
    _reset(engine)
    initialize_database(engine)
    try:
        yield engine
    finally:
        _reset(engine)
        engine.dispose()


@pytest.fixture
def clean_database(postgres_engine: Engine) -> None:
    from northstar_quant.data.db.base import Base

    quoted = [
        postgres_engine.dialect.identifier_preparer.quote(t.name)
        for t in Base.metadata.tables.values()
    ]
    quoted.append('"research_runs"')
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.exec_driver_sql(f"TRUNCATE TABLE {', '.join(quoted)} RESTART IDENTITY CASCADE")


@pytest.fixture
def session_factory(postgres_engine: Engine, clean_database: None) -> sessionmaker[Session]:
    del clean_database
    return sessionmaker(bind=postgres_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with session_factory() as session:
        yield session
