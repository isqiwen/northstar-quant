"""Read-only processing backlog observations; never claim or recover queued work."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, func, select, text

from .library import _attempts


@dataclass(frozen=True)
class ProcessingStatus:
    observed_at: datetime
    total: int
    pending: int
    running: int
    published: int
    failed: int
    oldest_pending_id: UUID | None
    oldest_pending_at: datetime | None
    oldest_pending_seconds: int | None


def processing_status(engine: Engine) -> ProcessingStatus:
    """Aggregate the entire queue in one database snapshot, independent of list pagination."""
    oldest = (
        select(_attempts.c.attempt_id)
        .where(_attempts.c.status == "PENDING")
        .order_by(_attempts.c.created_at, _attempts.c.attempt_id)
        .limit(1)
        .correlate(None)
        .scalar_subquery()
    )
    statement = select(
        func.statement_timestamp().label("observed_at"),
        func.count().label("total"),
        *(
            func.count().filter(_attempts.c.status == status).label(status.lower())
            for status in ("PENDING", "RUNNING", "PUBLISHED", "FAILED")
        ),
        oldest.label("oldest_pending_id"),
        func.min(_attempts.c.created_at)
        .filter(_attempts.c.status == "PENDING")
        .label("oldest_pending_at"),
    ).select_from(_attempts)
    with engine.begin() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '3s'"))
        row = dict(connection.execute(statement).mappings().one())
    row["oldest_pending_seconds"] = (
        max(0, int((row["observed_at"] - row["oldest_pending_at"]).total_seconds()))
        if row["oldest_pending_at"] is not None
        else None
    )
    return ProcessingStatus(**row)
