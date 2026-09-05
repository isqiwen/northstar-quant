"""One PostgreSQL configuration and explicit current-baseline initialization."""

from __future__ import annotations

import os
from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import make_url


def open_database() -> Engine:
    database_url = os.environ.get("NORTHSTAR_DATABASE_URL")
    if not database_url:
        raise ValueError("NORTHSTAR_DATABASE_URL must name your PostgreSQL database")
    try:
        parsed = make_url(database_url)
    except Exception as exc:
        raise ValueError("NORTHSTAR_DATABASE_URL is invalid") from exc
    if parsed.drivername != "postgresql+psycopg":
        raise ValueError("NORTHSTAR_DATABASE_URL must use postgresql+psycopg")
    return create_engine(parsed, pool_pre_ping=True)


def initialize_database(engine: Engine) -> None:
    """Install the sole current baseline on an empty database, or verify it."""

    from northstar_quant.runs import initialize_run_store

    configuration = Config()
    configuration.set_main_option(
        "script_location", str(files("northstar_quant.data").joinpath("migrations"))
    )
    with engine.begin() as connection:
        configuration.attributes["connection"] = connection
        existing = set(inspect(connection).get_table_names())
        if existing and "alembic_version" not in existing:
            raise ValueError("database is not empty and has no Northstar migration baseline")
        command.upgrade(configuration, "head")
    initialize_run_store(engine)


def require_current_database(engine: Engine) -> None:
    """Reject missing or retired database shapes without performing a write."""

    configuration = Config()
    configuration.set_main_option(
        "script_location", str(files("northstar_quant.data").joinpath("migrations"))
    )
    expected = set(ScriptDirectory.from_config(configuration).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
    if actual != expected:
        raise ValueError("database does not have the current Northstar baseline")
