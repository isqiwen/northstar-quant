"""P4-WP13 deterministic structured analogue matching for Event research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Mapping

from northstar_quant.intelligence.ontology import Ontology


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class AnalogueError(ValueError):
    pass


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise AnalogueError(f"{field} must be a non-empty identifier")
    return value.strip()


def _time(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AnalogueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class EventProfile:
    event_id: str
    event_type: str
    severity: int
    geography: str
    commodity_id: str
    inventory_regime: str
    usd_regime: str
    volatility_regime: str
    curve_regime: str
    event_time: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        for field in ("event_id", "event_type", "geography", "commodity_id", "inventory_regime", "usd_regime", "volatility_regime", "curve_regime"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if not isinstance(self.severity, int) or isinstance(self.severity, bool) or not 1 <= self.severity <= 5:
            raise AnalogueError("severity must be an integer in [1, 5]")
        event_time = _time(self.event_time, "event_time")
        available_at = _time(self.available_at, "available_at")
        if available_at < event_time:
            raise AnalogueError("available_at cannot precede event_time")
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)

    def is_available_at(self, simulation_time: datetime) -> bool:
        return self.available_at <= _time(simulation_time, "simulation_time")


@dataclass(frozen=True, slots=True)
class AnalogueMatch:
    reference_event_id: str
    analogue_event_id: str
    structured_similarity: float
    embedding_similarity: float | None

    def __post_init__(self) -> None:
        for field in ("reference_event_id", "analogue_event_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if self.reference_event_id == self.analogue_event_id:
            raise AnalogueError("an event cannot be its own analogue")
        if not 0 <= self.structured_similarity <= 1:
            raise AnalogueError("structured_similarity must be in [0, 1]")
        if self.embedding_similarity is not None and not 0 <= self.embedding_similarity <= 1:
            raise AnalogueError("embedding_similarity must be in [0, 1]")

    @property
    def score(self) -> float:
        """Embedding is optional and subordinate to the mandatory structured distance."""
        if self.embedding_similarity is None:
            return self.structured_similarity
        return 0.85 * self.structured_similarity + 0.15 * self.embedding_similarity


def _structured_similarity(reference: EventProfile, candidate: EventProfile) -> float:
    exact = (
        reference.event_type == candidate.event_type,
        reference.geography == candidate.geography,
        reference.commodity_id == candidate.commodity_id,
        reference.inventory_regime == candidate.inventory_regime,
        reference.usd_regime == candidate.usd_regime,
        reference.volatility_regime == candidate.volatility_regime,
        reference.curve_regime == candidate.curve_regime,
    )
    severity = 1 - abs(reference.severity - candidate.severity) / 4
    return (sum(exact) + severity) / 8


def rank_analogues(
    *,
    reference: EventProfile,
    candidates: tuple[EventProfile, ...],
    ontology: Ontology,
    simulation_time: datetime,
    embedding_similarities: Mapping[str, float] | None = None,
) -> tuple[AnalogueMatch, ...]:
    """Rank only known historical profiles; unstructured embeddings cannot replace it."""
    if not isinstance(reference, EventProfile) or not isinstance(ontology, Ontology):
        raise AnalogueError("reference and ontology must be typed")
    if not isinstance(candidates, tuple) or not all(isinstance(item, EventProfile) for item in candidates):
        raise AnalogueError("candidates must be a typed tuple")
    all_profiles = (reference, *candidates)
    if any(not profile.is_available_at(simulation_time) for profile in all_profiles):
        raise AnalogueError("all analogue profiles must be available at simulation_time")
    if any(profile.event_type not in ontology.event_types or profile.commodity_id not in ontology.commodities for profile in all_profiles):
        raise AnalogueError("analogue profiles must use ontology event types and commodities")
    embeddings = embedding_similarities or {}
    if not all(isinstance(event_id, str) and isinstance(score, float) and 0 <= score <= 1 for event_id, score in embeddings.items()):
        raise AnalogueError("embedding similarities must be bounded float values")
    matches = tuple(AnalogueMatch(reference.event_id, candidate.event_id, _structured_similarity(reference, candidate), embeddings.get(candidate.event_id)) for candidate in candidates if candidate.event_id != reference.event_id)
    return tuple(sorted(matches, key=lambda match: (-match.score, match.analogue_event_id)))


__all__ = ["AnalogueError", "AnalogueMatch", "EventProfile", "rank_analogues"]
