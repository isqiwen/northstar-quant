"""P4-WP11 point-in-time market context snapshots for intelligence research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math
import re

from northstar_quant.intelligence.ontology import Ontology


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class MarketContextError(ValueError):
    pass


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise MarketContextError(f"{field} must be a non-empty identifier")
    return value.strip()


def _time(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MarketContextError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MarketContextSnapshot:
    snapshot_id: str
    commodity_id: str
    market_id: str
    dataset_version: str
    as_of: datetime
    available_at: datetime
    inventory: float
    term_structure: float
    basis: float
    positioning: float
    volatility: float
    usd: float
    cny: float
    macro_regime: str
    seasonality: str

    def __post_init__(self) -> None:
        for field in ("snapshot_id", "commodity_id", "market_id", "dataset_version", "macro_regime", "seasonality"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        as_of = _time(self.as_of, "as_of")
        available_at = _time(self.available_at, "available_at")
        if available_at < as_of:
            raise MarketContextError("available_at cannot precede as_of")
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "available_at", available_at)
        for field in ("inventory", "term_structure", "basis", "positioning", "volatility", "usd", "cny"):
            value = getattr(self, field)
            if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value):
                raise MarketContextError(f"{field} must be a finite numeric observation")
            object.__setattr__(self, field, float(value))
        if self.volatility < 0:
            raise MarketContextError("volatility cannot be negative")
        if self.usd <= 0 or self.cny <= 0:
            raise MarketContextError("USD and CNY observations must be positive")

    def is_available_at(self, simulation_time: datetime) -> bool:
        return self.available_at <= _time(simulation_time, "simulation_time")


def context_as_of(*, snapshot: MarketContextSnapshot, ontology: Ontology, simulation_time: datetime) -> MarketContextSnapshot:
    """Return only a snapshot already available at the research simulation time."""
    if not isinstance(snapshot, MarketContextSnapshot) or not isinstance(ontology, Ontology):
        raise MarketContextError("snapshot and ontology must be typed")
    if snapshot.commodity_id not in ontology.commodities:
        raise MarketContextError("context commodity must be present in ontology")
    if not snapshot.is_available_at(simulation_time):
        raise MarketContextError("context snapshot is not yet available at simulation_time")
    return snapshot


__all__ = ["MarketContextError", "MarketContextSnapshot", "context_as_of"]
