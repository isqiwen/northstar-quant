"""P4-WP06 schema- and ontology-validated extracted-event candidates."""

from __future__ import annotations

from dataclasses import dataclass

from northstar_quant.intelligence.domain import Document, Evidence
from northstar_quant.intelligence.ontology import Ontology


class ExtractionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractedEvent:
    extraction_id: str
    document_id: str
    event_type: str
    ontology_version: str
    evidence: Evidence
    extraction_confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.extraction_id, str) or not self.extraction_id.strip():
            raise ExtractionError("extraction_id is required")
        if not 0 <= self.extraction_confidence <= 1:
            raise ExtractionError("extraction_confidence must be in [0, 1]")
        if self.evidence.document_id != self.document_id:
            raise ExtractionError("evidence must bind the extracted document")


def validate_extraction(*, document: Document, candidate: ExtractedEvent, ontology: Ontology) -> ExtractedEvent:
    if not isinstance(document, Document) or not isinstance(candidate, ExtractedEvent) or not isinstance(ontology, Ontology):
        raise ExtractionError("document, candidate and ontology must be typed")
    if candidate.document_id != document.document_id or candidate.evidence.content_hash != document.content_hash:
        raise ExtractionError("candidate evidence does not bind the source Document")
    if candidate.ontology_version != ontology.version:
        raise ExtractionError("candidate ontology_version does not match ontology")
    ontology.validate_event_type(candidate.event_type)
    return candidate


__all__ = ["ExtractedEvent", "ExtractionError", "validate_extraction"]
