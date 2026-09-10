"""Instance-local SQLite durability and writer ownership for Live."""

from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, event, inspect


class KernelLock:
    """A local process lock has no expiry and cannot authorize trading failover."""

    def __init__(self, database: Path) -> None:
        path = Path(str(database) + ".owner")
        self._path = path
        self._fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(self._fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("Live owner lock must be an owned regular file")
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._identity = (info.st_dev, info.st_ino)
        except BaseException:
            os.close(self._fd)
            raise

    def check(self) -> None:
        if self._fd < 0:
            raise ValueError("Live ownership lock is closed")
        info = self._path.lstat()
        if (info.st_dev, info.st_ino) != self._identity:
            raise ValueError("Live ownership lock was replaced; stop and reconcile required")

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1


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
    file_identity = (path.stat().st_dev, path.stat().st_ino)
    sqlite3.register_adapter(UUID, lambda value: value.hex)
    engine = create_engine("sqlite+pysqlite:///" + str(path), connect_args={"timeout": 2})

    @event.listens_for(engine, "connect")
    def connect(dbapi: Any, _: Any) -> None:
        dbapi.isolation_level = None
        dbapi.create_function("clock_timestamp", 0, lambda: datetime.now(UTC).isoformat())
        dbapi.execute("PRAGMA foreign_keys=ON")
        dbapi.execute("PRAGMA journal_mode=WAL")
        dbapi.execute("PRAGMA synchronous=FULL")
        dbapi.execute("PRAGMA busy_timeout=2000")

    @event.listens_for(engine, "begin")
    def begin(connection: Connection) -> None:
        info = path.stat()
        if (info.st_dev, info.st_ino) != file_identity:
            raise ValueError("Live database file changed; restart and reconcile required")
        connection.exec_driver_sql(
            "BEGIN IMMEDIATE" if connection.get_execution_options().get("live_write") else "BEGIN"
        )

    return engine


@contextmanager
def write_transaction(engine: Engine) -> Iterator[Connection]:
    with engine.connect().execution_options(live_write=True) as connection:
        with connection.begin():
            yield connection


def initialize(engine: Engine) -> None:
    """Install one current local Live store, including retained archive evidence."""
    from northstar_quant.accounting.baselines import initialize_broker_baselines
    from northstar_quant.accounting.funds import initialize_broker_funds
    from northstar_quant.accounting.ledger import initialize_broker_ledger
    from northstar_quant.accounting.stream_progress import initialize_stream_accounts
    from northstar_quant.broker.records import initialize_broker_records
    from northstar_quant.data_management.db.base import Base
    from northstar_quant.data_management.library import initialize_library
    from northstar_quant.execution.reviews import initialize_order_reviews
    from northstar_quant.live.commands import initialize_live_commands
    from northstar_quant.live.instances import initialize as initialize_binding
    from northstar_quant.live.materials import initialize_materials
    from northstar_quant.live.opening_budgets import initialize_opening_budgets
    from northstar_quant.live.streams import initialize_streams
    from northstar_quant.research.configurations import initialize_configuration_store
    from northstar_quant.research.factor_catalog import initialize_factor_catalog
    from northstar_quant.research.paper import initialize_paper_store

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
            initialize_factor_catalog,
            initialize_configuration_store,
            initialize_paper_store,
            initialize_broker_records,
            initialize_broker_baselines,
            initialize_broker_ledger,
            initialize_broker_funds,
            initialize_order_reviews,
            initialize_materials,
            initialize_streams,
            initialize_stream_accounts,
            initialize_opening_budgets,
            initialize_live_commands,
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
            BEGIN SELECT RAISE(ABORT, 'Live binding is immutable'); END
        """)


def require_current(engine: Engine) -> None:
    required = {
        "northstar_store",
        "live_instance_binding",
        "live_commands",
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
            or connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one()
            != "live"
        ):
            raise ValueError("Live storage requires current initialization")
