"""One PostgreSQL configuration and explicit current-baseline initialization."""

from __future__ import annotations

import os
from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
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


def initialize_database(engine: Engine, *, owner: str | None = None) -> None:
    """Install current tables explicitly, preserving all existing application facts."""

    from northstar_quant.accounting.baselines import initialize_broker_baselines
    from northstar_quant.accounting.funds import initialize_broker_funds
    from northstar_quant.accounting.ledger import initialize_broker_ledger
    from northstar_quant.accounting.stream_progress import initialize_stream_accounts
    from northstar_quant.broker.records import initialize_broker_records
    from northstar_quant.data_management.library import initialize_library
    from northstar_quant.data_management.maintenance import initialize_maintenance
    from northstar_quant.execution.reviews import initialize_order_reviews
    from northstar_quant.live.commands import initialize_live_commands
    from northstar_quant.live.materials import initialize_materials
    from northstar_quant.live.opening_budgets import initialize_opening_budgets
    from northstar_quant.live.streams import initialize_streams
    from northstar_quant.research.configurations import initialize_configuration_store
    from northstar_quant.research.factor_catalog import initialize_factor_catalog
    from northstar_quant.research.paper import initialize_paper_store
    from northstar_quant.research.runs import initialize_run_store
    from northstar_quant.research.strategy_management import initialize_strategy_management

    owner = owner or os.environ.get("NORTHSTAR_DATABASE_OWNER", "all")
    if owner not in {"all", "data_hub", "research", "live"}:
        raise ValueError("unknown database owner")
    configuration = Config()
    configuration.set_main_option(
        "script_location", str(files("northstar_quant.data_management").joinpath("migrations"))
    )
    with engine.begin() as connection:
        existing = set(inspect(connection).get_table_names())
        if existing and "northstar_store" not in existing:
            raise ValueError("use a new database for the current owned storage model")
        if "northstar_store" in existing:
            recorded = connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one()
            if recorded != owner:
                raise ValueError("database owner mismatch")
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS northstar_store (owner text PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO northstar_store VALUES (%s) ON CONFLICT DO NOTHING", (owner,)
        )
        if owner in {"all", "data_hub"}:
            configuration.attributes["connection"] = connection
            _require_git_identity_columns(connection)
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
        if owner in {"all", "data_hub"}:
            initialize_library(connection)
            from northstar_quant.data_management.tushare.jobs import initialize as initialize_sync

            initialize_sync(connection)
            initialize_maintenance(connection)
        if owner in {"all", "research"}:
            initialize_factor_catalog(connection)
            initialize_strategy_management(connection)
            initialize_run_store(connection)
            initialize_configuration_store(connection)
            initialize_paper_store(connection)
        if owner == "live":
            from northstar_quant.live.archive import initialize_archive

            initialize_archive(connection)
        if owner == "all":
            initialize_materials(connection)
            initialize_broker_records(connection)
            initialize_broker_baselines(connection)
            initialize_broker_ledger(connection)
            initialize_order_reviews(connection)
            initialize_streams(connection)
            initialize_stream_accounts(connection)
            initialize_opening_budgets(connection)
            initialize_broker_funds(connection)
            initialize_live_commands(connection)


def require_current_database(engine: Engine) -> None:
    """Reject missing or retired database shapes without performing a write."""

    configuration = Config()
    configuration.set_main_option(
        "script_location", str(files("northstar_quant.data_management").joinpath("migrations"))
    )
    expected = set(ScriptDirectory.from_config(configuration).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
        if "northstar_store" not in set(inspect(connection).get_table_names()):
            raise ValueError("database does not have the current owned baseline")
        owner = connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one()
        configured = os.environ.get("NORTHSTAR_DATABASE_OWNER")
        if configured and configured != owner:
            raise ValueError("database owner mismatch")
        present = set(inspect(connection).get_table_names())
        _require_git_identity_columns(connection)
    required = {
        "live_strategy_materials",
        "factor_revisions",
        "factor_annotations",
        "factor_runs",
        "strategy_versions",
        "strategy_candidates",
        "research_attempts",
        "research_runs",
        "paper_configurations",
        "paper_sessions",
        "paper_inputs",
        "paper_steps",
        "data_sources",
        "data_processing_attempts",
        "data_sync_jobs",
        "data_sync_settings",
        "data_sync_receipts",
        "data_sync_coverage",
        "data_sync_contracts",
        "data_sync_calendar",
        "data_sync_attempts",
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
        "broker_stream_accounts",
        "broker_opening_budgets",
        "broker_funds_entries",
        "live_commands",
    }
    if owner == "live":
        required = {"live_archive_records"}
    elif owner != "all":
        prefixes = {
            "data_hub": ("data_",),
            "research": ("research_", "paper_", "factor_", "strategy_"),
            "live": ("live_", "broker_"),
        }
        required = {name for name in required if name.startswith(prefixes[owner])}
    if (owner in {"all", "data_hub"} and actual != expected) or not required <= present:
        raise ValueError("database does not have the current Northstar baseline")


def _require_git_identity_columns(connection: Connection) -> None:
    inspector = inspect(connection)
    present = set(inspector.get_table_names())
    if "data_sync_jobs" in present and "identity" not in {
        c["name"] for c in inspector.get_columns("data_sync_jobs")
    }:
        raise ValueError("旧单次同步数据库不能用于自动同步；请保全备份后使用当前结构的新库")
    for table in (
        "research_runs",
        "paper_sessions",
        "data_processing_attempts",
        "data_sync_jobs",
        "broker_query_batches",
    ):
        if table in present and "code_revision" not in {
            column["name"] for column in inspector.get_columns(table)
        }:
            raise ValueError(
                "database predates Git code provenance; preserve its backup and use a "
                "current empty database, never relabel historical fingerprints as Git commits"
            )
