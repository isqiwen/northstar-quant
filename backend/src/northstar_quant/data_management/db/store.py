"""The sole Data Hub PostgreSQL schema, separate from Research and Live state."""

from __future__ import annotations

import os
from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import make_url

_REQUIRED = {
    "data_sources",
    "data_processing_attempts",
    "data_admission_rejections",
    "data_backups",
    "data_compactions",
    "data_cleanup_receipts",
    "data_sync_jobs",
    "data_sync_settings",
    "data_sync_receipts",
    "data_sync_coverage",
    "data_sync_contracts",
    "data_sync_calendar",
    "data_sync_attempts",
}


def open_store() -> Engine:
    database_url = os.environ.get("NORTHSTAR_DATABASE_URL")
    if not database_url:
        raise ValueError("NORTHSTAR_DATABASE_URL must name the Data Hub PostgreSQL database")
    try:
        parsed = make_url(database_url)
    except Exception as error:
        raise ValueError("NORTHSTAR_DATABASE_URL is invalid") from error
    if parsed.drivername != "postgresql+psycopg":
        raise ValueError("Data Hub requires postgresql+psycopg")
    return create_engine(parsed, pool_pre_ping=True, pool_timeout=2)


def _configuration() -> Config:
    configuration = Config()
    configuration.set_main_option(
        "script_location", str(files("northstar_quant.data_management").joinpath("migrations"))
    )
    return configuration


def _require(connection: Connection) -> None:
    from northstar_quant.data_management.catalog import models  # noqa: F401

    from .base import Base

    present = set(inspect(connection).get_table_names())
    if not ({"northstar_store"} | _REQUIRED | set(Base.metadata.tables)) <= present:
        raise ValueError("Data Hub requires the current initialized schema")
    if connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one() != "data_hub":
        raise ValueError("Data Hub database owner mismatch")
    if set(MigrationContext.configure(connection).get_current_heads()) != set(
        ScriptDirectory.from_config(_configuration()).get_heads()
    ):
        raise ValueError("Data Hub database does not have the current baseline")
    for table, required in {
        "data_sync_jobs": {"identity", "code_revision"},
        "data_processing_attempts": {"code_revision"},
    }.items():
        if not required <= {column["name"] for column in inspect(connection).get_columns(table)}:
            raise ValueError(
                "Data Hub stored implementation identities do not match the current schema"
            )


def initialize(engine: Engine) -> None:
    from northstar_quant.data_management.cleanup import initialize as initialize_cleanup
    from northstar_quant.data_management.compaction import initialize as initialize_compactions
    from northstar_quant.data_management.library import initialize_library
    from northstar_quant.data_management.maintenance import initialize_maintenance
    from northstar_quant.data_management.tushare.jobs import initialize as initialize_sync

    if engine.dialect.name != "postgresql":
        raise ValueError("Data Hub requires PostgreSQL")
    with engine.begin() as connection:
        if inspect(connection).get_table_names():
            _require(connection)
            return
        connection.exec_driver_sql(
            "CREATE TABLE northstar_store (owner TEXT PRIMARY KEY CHECK (owner='data_hub'))"
        )
        connection.exec_driver_sql("INSERT INTO northstar_store VALUES ('data_hub')")
        configuration = _configuration()
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "head")
        initialize_library(connection)
        initialize_sync(connection)
        initialize_maintenance(connection)
        initialize_compactions(connection)
        initialize_cleanup(connection)
        connection.exec_driver_sql("""
            CREATE FUNCTION preserve_data_owner() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Data Hub owner is immutable'; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER preserve_data_owner
              BEFORE UPDATE OR DELETE OR TRUNCATE ON northstar_store
              FOR EACH STATEMENT EXECUTE FUNCTION preserve_data_owner();
        """)
        _require(connection)


def require_current(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("Data Hub requires PostgreSQL")
    with engine.connect() as connection:
        _require(connection)
