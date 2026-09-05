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
    return create_engine(parsed, pool_pre_ping=True, pool_timeout=2)


def initialize_database(engine: Engine) -> None:
    """Install current tables explicitly, preserving all existing application facts."""

    from northstar_quant.broker.baselines import initialize_broker_baselines
    from northstar_quant.broker.budgets import initialize_opening_budgets
    from northstar_quant.broker.ledger import initialize_broker_ledger
    from northstar_quant.broker.records import initialize_broker_records
    from northstar_quant.broker.streams import initialize_streams
    from northstar_quant.data.library import initialize_library
    from northstar_quant.data.maintenance import initialize_maintenance
    from northstar_quant.runs import initialize_run_store
    from northstar_quant.sessions import initialize_session_store

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
        # Record ordinals belong to the actual format: CSV has a header; copied
        # JSON starts at record 1. Replace the format-specific assumption while
        # preserving every previously accepted source record.
        connection.exec_driver_sql("""
            ALTER TABLE import_record
                DROP CONSTRAINT IF EXISTS ck_import_record_record_row_number_header_offset;
            ALTER TABLE import_record
                DROP CONSTRAINT IF EXISTS ck_import_record_record_row_number_positive;
            ALTER TABLE import_record ADD CONSTRAINT ck_import_record_record_row_number_positive
                CHECK (source_row_number >= 1)
        """)
        initialize_run_store(connection)
        initialize_session_store(connection)
        initialize_library(connection)
        initialize_maintenance(connection)
        initialize_broker_records(connection)
        initialize_broker_baselines(connection)
        initialize_broker_ledger(connection)
        initialize_streams(connection)
        initialize_opening_budgets(connection)


def require_current_database(engine: Engine) -> None:
    """Reject missing or retired database shapes without performing a write."""

    configuration = Config()
    configuration.set_main_option(
        "script_location", str(files("northstar_quant.data").joinpath("migrations"))
    )
    expected = set(ScriptDirectory.from_config(configuration).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
        present = set(inspect(connection).get_table_names())
    required = {
        "research_runs",
        "paper_configurations",
        "paper_sessions",
        "paper_inputs",
        "paper_steps",
        "data_sources",
        "data_processing_attempts",
        "data_admission_rejections",
        "data_backups",
        "broker_query_batches",
        "broker_account_baselines",
        "broker_baseline_checks",
        "broker_position_entries",
        "broker_position_checks",
        "broker_order_checks",
        "broker_streams",
        "broker_stream_events",
        "broker_stream_steps",
        "broker_stream_commands",
        "broker_opening_budgets",
    }
    if actual != expected or not required <= present:
        raise ValueError("database does not have the current Northstar baseline")
