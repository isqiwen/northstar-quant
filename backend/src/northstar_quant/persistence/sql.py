"""UTC SQL values and explicit writer admission shared by local business owners."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, DateTime, Engine
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
