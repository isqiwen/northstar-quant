"""Research's local SQLite state, transaction boundaries and immutable facts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, event, inspect

from northstar_quant.persistence.sql import sqlite_engine, write_transaction


def open_store(path: Path | None = None) -> Engine:
    value = os.environ.get("NORTHSTAR_RESEARCH_DATABASE")
    if path is None and not value:
        raise ValueError("NORTHSTAR_RESEARCH_DATABASE must name a local SQLite file")
    path = path or Path(value or "")
    if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError("Research SQLite requires an absolute file in an existing local directory")
    mountinfo = Path("/proc/self/mountinfo")
    if mountinfo.is_file():
        matches = []
        for line in mountinfo.read_text().splitlines():
            before, _, after = line.partition(" - ")
            fields = before.split()
            if len(fields) >= 5 and after:
                mount = Path(fields[4].replace("\\040", " "))
                if path.resolve().is_relative_to(mount):
                    matches.append((len(str(mount)), after.split()[0]))
        if matches and max(matches)[1] in {"nfs", "nfs4", "cifs", "smb3", "fuse.sshfs"}:
            raise ValueError("Research SQLite must reside on local storage, not a network share")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if not path.is_file():
            raise ValueError("Research SQLite path is not a regular file") from None
    else:
        os.close(descriptor)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    engine = sqlite_engine(path, timeout=5)

    @event.listens_for(engine, "connect")
    def connect(dbapi: Any, _record: Any) -> None:
        dbapi.isolation_level = None
        dbapi.execute("PRAGMA foreign_keys=ON")
        dbapi.execute("PRAGMA journal_mode=WAL")
        dbapi.execute("PRAGMA synchronous=FULL")
        dbapi.execute("PRAGMA busy_timeout=5000")

    return engine


def immutable(connection: Connection, table: str) -> None:
    for action in ("UPDATE", "DELETE"):
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'Research facts are immutable'); END"
        )


def transitions(
    connection: Connection, table: str, fields: tuple[str, ...], terminal: tuple[str, ...]
) -> None:
    connection.exec_driver_sql(
        f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_DELETE BEFORE DELETE ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'Research attempts are immutable'); END"
    )
    changed = " OR ".join(f"OLD.{name} IS NOT NEW.{name}" for name in fields)
    states = ",".join(repr(state) for state in terminal)
    connection.exec_driver_sql(
        f"CREATE TRIGGER IF NOT EXISTS transition_{table} BEFORE UPDATE ON {table} "
        f"WHEN OLD.status <> 'RUNNING' OR NEW.status NOT IN ({states}) OR {changed} "
        "BEGIN SELECT RAISE(ABORT, 'Research attempt facts are immutable'); END"
    )


def initialize(engine: Engine) -> None:
    from .configurations import initialize_configuration_store
    from .factor_catalog import initialize_factor_catalog
    from .paper import initialize_paper_store
    from .runs import initialize_run_store
    from .strategy_management import initialize_strategy_management

    if engine.dialect.name != "sqlite":
        raise ValueError("Research uses local SQLite")
    with write_transaction(engine) as connection:
        present = set(inspect(connection).get_table_names())
        if present and "northstar_store" not in present:
            raise ValueError("Research storage is not an owned current database")
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS northstar_store (owner TEXT PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT OR IGNORE INTO northstar_store VALUES ('research')")
        if (
            connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one()
            != "research"
        ):
            raise ValueError("Research storage owner mismatch")
        initialize_factor_catalog(connection)
        initialize_strategy_management(connection)
        initialize_run_store(connection)
        initialize_configuration_store(connection)
        initialize_paper_store(connection)
        immutable(connection, "northstar_store")


def require_current(engine: Engine) -> None:
    with engine.connect() as connection:
        names = set(inspect(connection).get_table_names())
        required = {
            "northstar_store",
            "research_jobs",
            "research_job_attempts",
            "research_runs",
            "research_attempts",
            "factor_runs",
            "factor_revisions",
            "factor_annotations",
            "strategy_versions",
            "strategy_candidates",
            "paper_configurations",
            "paper_sessions",
            "paper_inputs",
            "paper_steps",
        }
        if (
            not required <= names
            or connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one()
            != "research"
        ):
            raise ValueError("Research storage requires current initialization")
