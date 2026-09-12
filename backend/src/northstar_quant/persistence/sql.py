"""UTC SQL values and explicit writer admission shared by local business owners."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, DateTime, Engine, create_engine, event
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stored evidence clocks are UTC even when the local DB stores no timezone."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evidence timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return (
            (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC))
            if value is not None
            else None
        )


@contextmanager
def write_transaction(engine: Engine) -> Iterator[Connection]:
    """Acquire the application's SQLite writer before reading state for modification.

    SQLite owners map northstar_write to BEGIN IMMEDIATE. PostgreSQL retains its
    ordinary transaction semantics and owner-specific advisory/row locks.
    """
    with engine.connect().execution_options(northstar_write=True) as connection:
        with connection.begin():
            yield connection


def sqlite_engine(path: Path, *, timeout: int) -> Engine:
    """Connect only to the file already admitted by its application owner.

    New pool connections must not create an empty database after file loss.
    The filename stays in Engine.url for owned backup and diagnostic operations.
    """
    info = path.stat()
    identity = info.st_dev, info.st_ino
    engine = create_engine("sqlite+pysqlite:///" + str(path), connect_args={"timeout": timeout})

    def check_identity() -> None:
        current = path.stat()
        if (current.st_dev, current.st_ino) != identity:
            raise ValueError("SQLite database file changed; restart and reconcile required")

    @event.listens_for(engine, "do_connect")
    def existing_file(
        _dialect: Any, _record: Any, arguments: list[Any], options: dict[str, Any]
    ) -> None:
        check_identity()
        arguments[0] = path.as_uri() + "?mode=rw"
        options["uri"] = True

    @event.listens_for(engine, "connect")
    def connected(_connection: Any, _record: Any) -> None:
        # Check again before application-specific connection pragmas can write.
        check_identity()

    @event.listens_for(engine, "begin")
    def begin(connection: Connection) -> None:
        check_identity()
        connection.exec_driver_sql(
            "BEGIN IMMEDIATE"
            if connection.get_execution_options().get("northstar_write")
            else "BEGIN"
        )

    return engine
