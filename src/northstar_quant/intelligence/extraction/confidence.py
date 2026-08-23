"""P4-WP08 evidence-backed confidence, never LLM self-confidence alone."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    source_trust: float
    cross_source_confirmation: float
    extraction_confidence: float
    entity_resolution_confidence: float

    def __post_init__(self) -> None:
        if any(not 0 <= value <= 1 for value in (self.source_trust, self.cross_source_confirmation, self.extraction_confidence, self.entity_resolution_confidence)):
            raise ValueError("confidence inputs must be in [0, 1]")

    @property
    def final_confidence(self) -> float:
        return self.source_trust * self.cross_source_confirmation * self.extraction_confidence * self.entity_resolution_confidence


__all__ = ["ConfidenceInputs"]
