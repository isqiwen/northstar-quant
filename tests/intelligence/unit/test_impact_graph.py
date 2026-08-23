from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from northstar_quant.intelligence.domain import Entity, Event, Evidence, Impact
from northstar_quant.intelligence.extraction import ExtractedEvent
from northstar_quant.intelligence.impact_graph import ContractRef, ImpactGraphError, ImpactNodeType, InstrumentRef, MarketRef, build_impact_path
from northstar_quant.intelligence.mechanisms import MechanismType, assess_mechanism
from northstar_quant.intelligence.ontology import load_ontology


def _inputs():
    ontology = load_ontology(Path("ontology"))
    now = datetime(2026, 8, 22, 9, tzinfo=UTC)
    content_hash = sha256(b"mine outage").hexdigest()
    evidence = Evidence("doc-1", content_hash, 0, 4)
    candidate = ExtractedEvent("extract-1", "doc-1", "SUPPLY", "v1", evidence, 0.8)
    assessment = assess_mechanism(candidate=candidate, mechanism_type=MechanismType.SUPPLY_REDUCTION, ontology=ontology, rationale="Outage evidence", confidence=0.8)
    event = Event("event-1", "v1", (evidence,), assessment.domain_mechanism, (Impact("impact-1", "copper", "UP"),))
    return ontology, event, assessment, Entity("mine-1", "Mine", "Example Mine"), now


def test_impact_path_preserves_the_required_event_to_contract_sequence():
    ontology, event, assessment, entity, _ = _inputs()
    path = build_impact_path(event=event, assessment=assessment, affected_entity=entity, commodity_id="copper", market=MarketRef("shfe", "copper"), instrument=InstrumentRef("cu", "shfe", "copper"), contract=ContractRef("cu2609", "cu", "copper"), ontology=ontology)
    assert tuple(node.node_type for node in path.nodes) == tuple(ImpactNodeType)
    assert path.nodes[-1].node_id == "cu2609"
    assert len(path.edges) == 6


def test_impact_path_rejects_unmodelled_commodity_or_broken_contract_mapping():
    ontology, event, assessment, entity, _ = _inputs()
    with pytest.raises(ImpactGraphError, match="present in ontology"):
        build_impact_path(event=event, assessment=assessment, affected_entity=entity, commodity_id="lithium", market=MarketRef("shfe", "lithium"), instrument=InstrumentRef("li", "shfe", "lithium"), contract=ContractRef("li2609", "li", "lithium"), ontology=ontology)
    with pytest.raises(ImpactGraphError, match="market mapping"):
        build_impact_path(event=event, assessment=assessment, affected_entity=entity, commodity_id="copper", market=MarketRef("shfe", "copper"), instrument=InstrumentRef("cu", "shfe", "copper"), contract=ContractRef("cu2609", "other", "copper"), ontology=ontology)


def test_impact_path_rejects_mechanism_evidence_not_retained_by_its_event():
    ontology, event, _, entity, _ = _inputs()
    unrelated_evidence = Evidence("doc-2", sha256(b"other outage").hexdigest(), 0, 5)
    unrelated_candidate = ExtractedEvent("extract-2", "doc-2", "SUPPLY", "v1", unrelated_evidence, 0.8)
    unrelated_assessment = assess_mechanism(candidate=unrelated_candidate, mechanism_type=MechanismType.SUPPLY_REDUCTION, ontology=ontology, rationale="Other outage evidence", confidence=0.8)

    with pytest.raises(ImpactGraphError, match="assessment evidence must be retained"):
        build_impact_path(event=event, assessment=unrelated_assessment, affected_entity=entity, commodity_id="copper", market=MarketRef("shfe", "copper"), instrument=InstrumentRef("cu", "shfe", "copper"), contract=ContractRef("cu2609", "cu", "copper"), ontology=ontology)
