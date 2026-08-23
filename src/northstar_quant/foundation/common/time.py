"""Time helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current UTC datetime as a timezone-aware value."""

    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime:
    """Normalize a datetime to UTC."""

    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
