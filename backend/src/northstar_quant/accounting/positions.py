"""Project same-day gross quantities from deduplicated confirmed fills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PositionChange:
    """Quantity effect of an identified fill; it asserts no fee or cash amount."""

    contract_id: UUID
    trading_day: date
    direction: str
    offset: str
    quantity_lots: int
    filled_at: datetime


def project_intraday_positions(
    trading_day: date, changes: tuple[PositionChange, ...]
) -> dict[UUID, dict[str, int]]:
    """Project gross positions from a flat start in one trading day.

    The caller supplies deduplicated confirmed fills. Opposite opens remain
    separate holdings. This quantity projection needs neither inferred fees nor
    an invented ordering among exchange fills with the same timestamp. There
    is no settlement, yesterday inventory or automatic reversal on over-close.
    """

    positions: dict[UUID, dict[str, int]] = {}
    groups: dict[datetime, list[PositionChange]] = {}
    for change in changes:
        if (
            not isinstance(change.contract_id, UUID)
            or type(change.trading_day) is not date
            or change.trading_day != trading_day
            or change.direction not in {"BUY", "SELL"}
            or change.offset not in {"OPEN", "CLOSE_TODAY", "CLOSE_YESTERDAY"}
            or type(change.quantity_lots) is not int
            or not 1 <= change.quantity_lots <= 1_000_000_000
            or not isinstance(change.filled_at, datetime)
            or change.filled_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("position effect requires a supported, same-day confirmed fill")
        groups.setdefault(change.filled_at, []).append(change)
    for moment in sorted(groups):
        for change in groups[moment]:
            position = positions.setdefault(
                change.contract_id,
                {"long_today": 0, "short_today": 0, "long_yesterday": 0, "short_yesterday": 0},
            )
            opening = change.offset == "OPEN"
            direction = "long" if (change.direction == "BUY") == opening else "short"
            age = "yesterday" if change.offset == "CLOSE_YESTERDAY" else "today"
            position[f"{direction}_{age}"] += change.quantity_lots * (1 if opening else -1)
        # A later open cannot repair an earlier close from a missing position.
        # Within the same reported second no fictitious exchange order is chosen.
        if any(value < 0 for position in positions.values() for value in position.values()):
            raise ValueError(
                "confirmed closes exceed the established position; missing facts remain"
            )
    return positions
