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

    def __post_init__(self) -> None:
        if (
            not isinstance(self.contract_id, UUID)
            or type(self.trading_day) is not date
            or self.direction not in {"BUY", "SELL"}
            or self.offset not in {"OPEN", "CLOSE_TODAY", "CLOSE_YESTERDAY"}
            or type(self.quantity_lots) is not int
            or not 1 <= self.quantity_lots <= 1_000_000_000
            or not isinstance(self.filled_at, datetime)
            or self.filled_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("position effect requires a supported, same-day confirmed fill")


@dataclass(frozen=True, slots=True)
class Position:
    """Reconstructible gross quantities for one contract and trading day."""

    long_today: int = 0
    short_today: int = 0
    long_yesterday: int = 0
    short_yesterday: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in self.to_dict().values()):
            raise ValueError(
                "confirmed closes exceed the established position; missing facts remain"
            )

    @property
    def net_lots(self) -> int:
        return self.long_today + self.long_yesterday - self.short_today - self.short_yesterday

    def to_dict(self) -> dict[str, int]:
        return {
            "long_today": self.long_today,
            "short_today": self.short_today,
            "long_yesterday": self.long_yesterday,
            "short_yesterday": self.short_yesterday,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Position:
        if set(value) != {"long_today", "short_today", "long_yesterday", "short_yesterday"}:
            raise ValueError("position requires all four gross quantities")
        quantities: dict[str, int] = {}
        for key, quantity in value.items():
            if type(quantity) is not int:
                raise ValueError("position requires integer lots")
            quantities[key] = quantity
        return cls(**quantities)

    def apply(self, changes: tuple[PositionChange, ...]) -> Position:
        """Apply a single fact or an unordered group at one reported instant.

        Callers deduplicate facts and bind contract/day. No change mutates this
        projection. Opposite opens never consume each other's holdings.
        """
        values = self.to_dict()
        for change in changes:
            if (change.contract_id, change.trading_day, change.filled_at) != (
                changes[0].contract_id,
                changes[0].trading_day,
                changes[0].filled_at,
            ):
                raise ValueError("position effect requires a supported, same-day confirmed fill")
            opening = change.offset == "OPEN"
            direction = "long" if (change.direction == "BUY") == opening else "short"
            age = "yesterday" if change.offset == "CLOSE_YESTERDAY" else "today"
            values[f"{direction}_{age}"] += change.quantity_lots * (1 if opening else -1)
        return Position(**values)


def project_intraday_positions(
    trading_day: date, changes: tuple[PositionChange, ...]
) -> dict[UUID, dict[str, int]]:
    """Project deduplicated broker fills from a flat start in one trading day.

    Within the same reported second no fictitious exchange order is chosen.
    A later open cannot repair an earlier close from a missing position.
    """
    positions: dict[UUID, Position] = {}
    groups: dict[datetime, dict[UUID, list[PositionChange]]] = {}
    for change in changes:
        if change.trading_day != trading_day:
            raise ValueError("position effect requires a supported, same-day confirmed fill")
        groups.setdefault(change.filled_at, {}).setdefault(change.contract_id, []).append(change)
    for moment in sorted(groups):
        for contract, group in groups[moment].items():
            positions[contract] = positions.get(contract, Position()).apply(tuple(group))
    return {contract: position.to_dict() for contract, position in positions.items()}
