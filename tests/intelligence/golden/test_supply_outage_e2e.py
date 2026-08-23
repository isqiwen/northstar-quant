"""P4-WP15 offline golden corpus: Document → Event → research Feature handoff."""

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

import pytest

from northstar_quant.intelligence.context import MarketContextSnapshot, context_as_of
from northstar_quant.intelligence.domain import Entity, Event, Evidence, Impact
from northstar_quant.intelligence.entity_resolution import CanonicalEntity, EntityResolver
from northstar_quant.intelligence.event_study import EventStudyResult, EventWindow, event_study_as_of
from northstar_quant.intelligence.extraction import ConfidenceInputs, ExtractedEvent, validate_extraction
from northstar_quant.intelligence.feature_projection import (
    AuthorizedMarketContext,
    EventEvidenceAvailability,
    IntelligenceFeatureProjectionRequest,
    IntelligenceFeatureProjector,
    IntelligenceMetricKind,
    IntelligenceMetricValue,
)
from northstar_quant.intelligence.impact_graph import ContractRef, InstrumentRef, MarketRef, build_impact_path
from northstar_quant.intelligence.ingestion import DedupDocument, RawDocument, cluster_documents, normalize_document
from northstar_quant.intelligence.mechanisms import MechanismType, assess_mechanism
from northstar_quant.intelligence.ontology import load_ontology
from northstar_quant.research.features import FeatureRegistry, register_canonical_feature
from northstar_quant.research.features.intelligence import EVENT_CONFIDENCE


def _fixture() -> dict[str, object]:
    return json.loads((Path(__file__).parent / "supply_outage_copper.json").read_text(encoding="utf-8"))


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@pytest.mark.golden
@pytest.mark.e2e
def test_supply_outage_golden_corpus_preserves_evidence_pit_and_feature_registry_handoff(tmp_path):
    fixture = _fixture()
    published_at = datetime.fromisoformat(str(fixture["published_at"]))
    collected_at = datetime.fromisoformat(str(fixture["collected_at"]))
    document = normalize_document(source_id=str(fixture["source_id"]), raw=RawDocument(str(fixture["canonical_url"]), str(fixture["content"]), published_at, str(fixture["license_classification"])), collected_at=collected_at)
    assert cluster_documents(documents=(DedupDocument(document, str(fixture["title"]), "copper-outage"),))[0].document_ids == (document.document_id,)
    entity_payload = fixture["entity"]
    assert isinstance(entity_payload, dict)
    resolved = EntityResolver(entities=(CanonicalEntity(str(entity_payload["entity_id"]), str(entity_payload["entity_type"]), str(entity_payload["canonical_name"]), (str(entity_payload["alias"]),)),)).resolve(str(entity_payload["alias"]))
    ontology = load_ontology(Path("ontology"))
    evidence = Evidence(document.document_id, document.content_hash, 0, 5)
    candidate = ExtractedEvent("extract-copper-outage", document.document_id, "SUPPLY", ontology.version, evidence, 0.7)
    validate_extraction(document=document, candidate=candidate, ontology=ontology)
    confidence_payload = fixture["confidence"]
    assert isinstance(confidence_payload, dict)
    confidence = ConfidenceInputs(**{key: float(value) for key, value in confidence_payload.items() if key != "expected_final"})
    assert confidence.final_confidence == pytest.approx(float(confidence_payload["expected_final"]))
    assessment = assess_mechanism(candidate=candidate, mechanism_type=MechanismType.SUPPLY_REDUCTION, ontology=ontology, rationale="The source evidence reports a mine outage.", confidence=confidence.final_confidence)
    commodity_id = str(fixture["commodity_id"])
    event = Event(str(fixture["event_id"]), ontology.version, (evidence,), assessment.domain_mechanism, (Impact("impact-copper-outage", commodity_id, "UP"),))
    path = build_impact_path(event=event, assessment=assessment, affected_entity=Entity(resolved.entity_id, resolved.entity_type, resolved.canonical_name), commodity_id=commodity_id, market=MarketRef(str(fixture["market_id"]), commodity_id), instrument=InstrumentRef(str(fixture["instrument_id"]), str(fixture["market_id"]), commodity_id), contract=ContractRef(str(fixture["contract_id"]), str(fixture["instrument_id"]), commodity_id), ontology=ontology)
    assert path.nodes[-1].node_id == fixture["contract_id"]
    context = MarketContextSnapshot("context-copper-outage", commodity_id, str(fixture["market_id"]), _hash("copper-outage-context-dataset"), published_at, collected_at, 100.0, -0.1, 2.0, 0.2, 0.18, 100.0, 7.2, "slowdown", "Q3")
    assert context_as_of(snapshot=context, ontology=ontology, simulation_time=collected_at) is context
    authorized_context = AuthorizedMarketContext(
        context,
        _hash("copper-outage-context-dataset"),
        _hash("copper-outage-context-publication-receipt"),
        _hash("copper-outage-context-artifact-snapshot"),
    )
    evidence_availability = EventEvidenceAvailability(
        document.document_id,
        document.content_hash,
        0,
        5,
        collected_at,
        _hash("copper-outage-document-publication-receipt"),
        _hash("copper-outage-document-source-artifact-snapshot"),
    )
    metric_values = tuple(
        IntelligenceMetricValue(
            kind=kind,
            score=(confidence.final_confidence if kind is IntelligenceMetricKind.EVENT_CONFIDENCE else None),
            missing_reason=(None if kind is IntelligenceMetricKind.EVENT_CONFIDENCE else "not_implemented"),
        )
        for kind in IntelligenceMetricKind
    )
    projection = IntelligenceFeatureProjector().project(
        IntelligenceFeatureProjectionRequest(
            projection_version="p8-golden-projection-v3",
            ontology=ontology,
            event=event,
            mechanism=event.mechanism,
            selected_impact=event.impacts[0],
            event_evidence=(evidence_availability,),
            authorized_market_context=authorized_context,
            event_time=published_at,
            available_at=collected_at,
            metric_values=metric_values,
            publication_receipt_hashes=tuple(
                sorted(
                    {
                        evidence_availability.source_publication_receipt_hash,
                        authorized_context.context_publication_receipt_hash,
                    }
                )
            ),
        )
    )
    projection_row = projection.as_feature_input_rows()[0]
    assert projection_row["event_hash"] == event.event_hash
    assert projection_row["event_confidence_input"] == pytest.approx(confidence.final_confidence)
    assert projection_row["supply_risk_1h_input"] is None
    assert projection.eligible_for_trading is False
    study_end = published_at + EventWindow.T_PLUS_15_MINUTES.duration
    study = EventStudyResult("study-copper-outage", event.event_id, "intelligence-golden-v1", EventWindow.T_PLUS_15_MINUTES, published_at, study_end, study_end, 0.01, 0.2, 1_000.0, 500.0, 1.0, 0.5, 0.02, -0.01)
    assert event_study_as_of(result=study, simulation_time=study_end) is study
    registry = FeatureRegistry()
    version = register_canonical_feature(registry, feature_id=EVENT_CONFIDENCE.feature_id, version="1.0.0", code_revision="p4-wp15-golden")
    assert registry.get_version(version.version_hash).feature_id == "intelligence.event_confidence"
    assert not hasattr(event, "target_weight") and not hasattr(path, "broker_order")
    assert collected_at < study_end and not study.is_available_at(collected_at)
