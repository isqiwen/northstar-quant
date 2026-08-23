"""P4-WP07 canonical event merge with out-of-order protection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from northstar_quant.intelligence.extraction import ExtractedEvent


class EventLifecycle(str, Enum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    UPDATED = "UPDATED"
    RESOLVED = "RESOLVED"
    RETRACTED = "RETRACTED"


@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    canonical_event_id: str
    semantic_key: str
    lifecycle: EventLifecycle
    observed_at: datetime
    extractions: tuple[ExtractedEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_event_id, str) or not self.canonical_event_id.strip():
            raise ValueError("canonical_event_id is required")
        if (
            not isinstance(self.semantic_key, str)
            or not self.semantic_key.strip()
            or self.semantic_key != self.semantic_key.strip()
        ):
            raise ValueError("semantic_key is required")
        if not isinstance(self.lifecycle, EventLifecycle):
            raise ValueError("lifecycle must be an EventLifecycle")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be timezone-aware")
        if not isinstance(self.extractions, tuple) or not self.extractions:
            raise ValueError("canonical event requires non-empty ExtractedEvent lineage")
        if not all(isinstance(item, ExtractedEvent) for item in self.extractions):
            raise ValueError("canonical event lineage must contain ExtractedEvent records")
        extraction_ids = tuple(item.extraction_id for item in self.extractions)
        if len(set(extraction_ids)) != len(extraction_ids):
            raise ValueError("canonical event lineage cannot contain duplicate extraction IDs")
        canonical_extractions = tuple(
            sorted(self.extractions, key=lambda item: item.extraction_id)
        )
        object.__setattr__(self, "extractions", canonical_extractions)

    @property
    def extraction_ids(self) -> tuple[str, ...]:
        """Stable identity-only view for callers that do not need evidence spans."""

        return tuple(item.extraction_id for item in self.extractions)


_ALLOWED_LIFECYCLE_TRANSITIONS: dict[EventLifecycle, frozenset[EventLifecycle]] = {
    EventLifecycle.OPEN: frozenset(
        {EventLifecycle.OPEN, EventLifecycle.CONFIRMED, EventLifecycle.RETRACTED}
    ),
    EventLifecycle.CONFIRMED: frozenset(
        {
            EventLifecycle.CONFIRMED,
            EventLifecycle.UPDATED,
            EventLifecycle.RESOLVED,
            EventLifecycle.RETRACTED,
        }
    ),
    EventLifecycle.UPDATED: frozenset(
        {EventLifecycle.UPDATED, EventLifecycle.RESOLVED, EventLifecycle.RETRACTED}
    ),
    EventLifecycle.RESOLVED: frozenset({EventLifecycle.RESOLVED, EventLifecycle.RETRACTED}),
    EventLifecycle.RETRACTED: frozenset({EventLifecycle.RETRACTED}),
}


def _merged_extractions(
    *, current: CanonicalEvent, candidate: ExtractedEvent
) -> tuple[ExtractedEvent, ...]:
    """Append one new source lineage item without permitting identity collision."""

    by_id = {item.extraction_id: item for item in current.extractions}
    existing = by_id.get(candidate.extraction_id)
    if existing is not None:
        if existing != candidate:
            raise ValueError("extraction ID collision has inconsistent evidence")
        return current.extractions
    return tuple(sorted((*current.extractions, candidate), key=lambda item: item.extraction_id))


def merge_event(
    *,
    current: CanonicalEvent | None,
    candidate: ExtractedEvent,
    semantic_key: str,
    observed_at: datetime,
    lifecycle: EventLifecycle,
) -> CanonicalEvent:
    """Merge source lineage without allowing stale or conflicting lifecycle rewrites."""

    if (
        not isinstance(candidate, ExtractedEvent)
        or not isinstance(semantic_key, str)
        or not semantic_key.strip()
        or semantic_key != semantic_key.strip()
        or not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or not isinstance(lifecycle, EventLifecycle)
    ):
        raise ValueError("typed candidate, semantic_key, lifecycle and timezone-aware observed_at are required")
    if current is None:
        return CanonicalEvent(
            f"event:{semantic_key}",
            semantic_key,
            lifecycle,
            observed_at,
            (candidate,),
        )
    if current.semantic_key != semantic_key:
        raise ValueError("cannot merge candidate into another semantic key")
    merged_extractions = _merged_extractions(current=current, candidate=candidate)
    if merged_extractions == current.extractions:
        return current
    if observed_at < current.observed_at:
        return CanonicalEvent(
            current.canonical_event_id,
            current.semantic_key,
            current.lifecycle,
            current.observed_at,
            merged_extractions,
        )
    if observed_at == current.observed_at:
        if lifecycle is not current.lifecycle:
            raise ValueError("equal observed_at lifecycle conflict is unsafe")
        return CanonicalEvent(
            current.canonical_event_id,
            current.semantic_key,
            current.lifecycle,
            current.observed_at,
            merged_extractions,
        )
    if lifecycle not in _ALLOWED_LIFECYCLE_TRANSITIONS[current.lifecycle]:
        raise ValueError(
            f"lifecycle transition {current.lifecycle.value} -> {lifecycle.value} is not permitted"
        )
    return CanonicalEvent(
        current.canonical_event_id,
        semantic_key,
        lifecycle,
        observed_at,
        merged_extractions,
    )


__all__ = ["CanonicalEvent", "EventLifecycle", "merge_event"]
