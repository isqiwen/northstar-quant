"""Instance-local SQLite durability and writer ownership for Live."""

from __future__ import annotations

import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, event, inspect

from northstar_quant.persistence.sql import sqlite_engine, write_transaction


def require_local_path(path: Path) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError("Live SQLite requires an absolute file in an existing local directory")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Live SQLite paths cannot contain symbolic links")
    mountinfo = Path("/proc/self/mountinfo")
    if mountinfo.is_file():
        matches = []
        for line in mountinfo.read_text().splitlines():
            before, _, after = line.partition(" - ")
            fields = before.split()
            if len(fields) >= 5 and after:
                mount = Path(fields[4].replace("\\040", " "))
                if path.is_relative_to(mount):
                    matches.append((len(str(mount)), after.split()[0]))
        if matches and max(matches)[1] in {"nfs", "nfs4", "cifs", "smb3", "fuse.sshfs"}:
            raise ValueError("Live SQLite must use local storage")


def open_store(path: Path) -> Engine:
    require_local_path(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Live SQLite must be an owner-only regular file")
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    sqlite3.register_adapter(UUID, lambda value: value.hex)
    engine = sqlite_engine(path, timeout=2)

    @event.listens_for(engine, "connect")
    def connect(dbapi: Any, _: Any) -> None:
        dbapi.isolation_level = None
        dbapi.create_function("clock_timestamp", 0, lambda: datetime.now(UTC).isoformat())
        dbapi.execute("PRAGMA foreign_keys=ON")
        dbapi.execute("PRAGMA journal_mode=WAL")
        dbapi.execute("PRAGMA synchronous=FULL")
        dbapi.execute("PRAGMA busy_timeout=2000")

    return engine


def initialize(engine: Engine) -> None:
    """Install one current local Live store, including retained archive evidence."""
    from northstar_quant.accounting.baselines import initialize_broker_baselines
    from northstar_quant.accounting.funds import initialize_broker_funds
    from northstar_quant.accounting.ledger import initialize_broker_ledger
    from northstar_quant.accounting.stream_progress import initialize_stream_accounts
    from northstar_quant.broker.execution_reports import initialize as initialize_ctp_receipts
    from northstar_quant.broker.order_transport import initialize as initialize_ctp_orders
    from northstar_quant.broker.records import initialize_broker_records
    from northstar_quant.data_management.db.base import Base
    from northstar_quant.data_management.library import initialize_library
    from northstar_quant.execution.journal import initialize_journal
    from northstar_quant.execution.reviews import initialize_order_reviews
    from northstar_quant.live.commands import initialize_live_commands
    from northstar_quant.live.execution_authority import (
        initialize as initialize_execution_authority,
    )
    from northstar_quant.live.instances import initialize as initialize_binding
    from northstar_quant.live.materials import initialize_materials
    from northstar_quant.live.opening_budgets import initialize_opening_budgets
    from northstar_quant.live.streams import initialize_streams

    if engine.dialect.name != "sqlite":
        raise ValueError("Live requires local SQLite")
    with write_transaction(engine) as connection:
        present = set(inspect(connection).get_table_names())
        if present and "northstar_store" not in present:
            raise ValueError("Live storage is not an owned current database")
        if (
            "northstar_store" in present
            and connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one()
            != "live"
        ):
            raise ValueError("Live storage owner mismatch")
        Base.metadata.create_all(connection)
        from northstar_quant.data_management.catalog.integrity import initialize_sqlite

        initialize_sqlite(connection)
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS northstar_store (owner TEXT PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT OR IGNORE INTO northstar_store VALUES ('live')")
        if connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one() != "live":
            raise ValueError("Live storage owner mismatch")
        for install in (
            initialize_library,
            initialize_broker_records,
            initialize_broker_baselines,
            initialize_broker_ledger,
            initialize_broker_funds,
            initialize_order_reviews,
            initialize_journal,
            initialize_ctp_orders,
            initialize_ctp_receipts,
            initialize_materials,
            initialize_streams,
            initialize_stream_accounts,
            initialize_opening_budgets,
            initialize_live_commands,
            initialize_execution_authority,
            initialize_binding,
        ):
            install(connection)
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS live_owner_{action} "
                f"BEFORE {action} ON northstar_store "
                "BEGIN SELECT RAISE(ABORT, 'Live storage owner is immutable'); END"
            )
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS live_binding_delete
            BEFORE DELETE ON live_instance_binding
            BEGIN SELECT RAISE(ABORT, 'Live binding is immutable'); END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS live_binding_update
            BEFORE UPDATE ON live_instance_binding
            WHEN OLD.account_id <> '' OR OLD.singleton IS NOT NEW.singleton
                OR OLD.instance_id IS NOT NEW.instance_id
                OR OLD.environment IS NOT NEW.environment OR OLD.broker_id IS NOT NEW.broker_id
                OR OLD.broker_profile IS NOT NEW.broker_profile
            BEGIN SELECT RAISE(ABORT, 'Live binding is immutable'); END
        """)


def require_current(engine: Engine) -> None:
    required = {
        "live_execution_authorizations",
        "live_execution_revocations",
        "northstar_store",
        "live_instance_binding",
        "live_commands",
        "ctp_order_receipts",
        "ctp_exchange_orders",
        "ctp_order_bindings",
        "ctp_requests",
        "execution_orders",
        "execution_order_events",
        "broker_streams",
        "broker_stream_events",
        "broker_stream_accounts",
        "broker_query_batches",
        "broker_account_baselines",
        "broker_position_entries",
        "broker_funds_entries",
        "live_strategy_materials",
        "data_sources",
    }
    with engine.connect() as connection:
        names = set(inspect(connection).get_table_names())
        if (
            not required <= names
            or "broker_profile"
            not in {
                column["name"]
                for column in inspect(connection).get_columns("live_instance_binding")
            }
            or "configuration_id"
            not in {
                column["name"]
                for column in inspect(connection).get_columns("live_strategy_materials")
            }
            or connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one()
            != "live"
        ):
            raise ValueError("Live storage requires current initialization")
