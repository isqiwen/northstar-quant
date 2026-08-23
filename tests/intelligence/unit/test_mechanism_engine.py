from hashlib import sha256
from pathlib import Path

import pytest

from northstar_quant.intelligence.domain import Evidence
from northstar_quant.intelligence.extraction import ExtractedEvent
from northstar_quant.intelligence.mechanisms import MechanismError, MechanismType, assess_mechanism
from northstar_quant.intelligence.ontology import load_ontology


def _candidate(event_type: str = "SUPPLY") -> ExtractedEvent:
    content_hash = sha256(b"mine outage").hexdigest()
    return ExtractedEvent("extract-1", "doc-1", event_type, "v1", Evidence("doc-1", content_hash, 0, 4), 0.8)


def test_mechanism_assessment_is_ontology_validated_evidence_backed_and_not_a_signal():
    assessment = assess_mechanism(
        candidate=_candidate(),
        mechanism_type=MechanismType.SUPPLY_REDUCTION,
        ontology=load_ontology(Path("ontology")),
        rationale="The evidence reports an outage.",
        confidence=0.7,
    )
    assert assessment.domain_mechanism.mechanism_id == "SUPPLY_REDUCTION"
    assert assessment.evidence.document_id == "doc-1"
    assert not hasattr(assessment, "signal")


def test_mechanism_engine_rejects_an_unrelated_event_type_and_unsupported_taxonomy_value():
    ontology = load_ontology(Path("ontology"))
    with pytest.raises(MechanismError, match="not permitted"):
        assess_mechanism(candidate=_candidate("DEMAND"), mechanism_type=MechanismType.SUPPLY_REDUCTION, ontology=ontology, rationale="not evidence-backed", confidence=0.5)
    with pytest.raises(MechanismError, match="present in ontology"):
        altered = type(ontology)(ontology.version, ontology.event_types, frozenset(), ontology.entity_types, ontology.commodities, ontology.relations)
        assess_mechanism(candidate=_candidate(), mechanism_type=MechanismType.SUPPLY_REDUCTION, ontology=altered, rationale="evidence-backed", confidence=0.5)
