"""P10 fixture-only six-commodity Document → Event → Feature-definition corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from northstar_quant.intelligence.domain import Event, Impact
from northstar_quant.intelligence.entity_resolution import CanonicalEntity, EntityResolver
from northstar_quant.intelligence.event_merge import EventLifecycle, merge_event
from northstar_quant.intelligence.ingestion import DedupDocument, cluster_documents
from northstar_quant.intelligence.mechanisms import assess_mechanism
from northstar_quant.intelligence.ontology import load_ontology
from northstar_quant.research.features import FeatureRegistry, register_canonical_feature
from northstar_quant.research.features.intelligence import INTELLIGENCE_FEATURE_DEFINITIONS
from tests.intelligence.golden._fixture_corpus import (
    FixtureCase,
    FixtureOnlyFeatureDefinitionHandoff,
    load_fixture_only_corpus,
    materialize_case_documents,
)


CORPUS_PATH = Path(__file__).with_name("six_commodity_fixture_only_v1.json")
_EXPECTED_FEATURE_DEFINITION_PATHS: dict[str, tuple[str, tuple[str, ...]]] = {
    "copper": (
        "intelligence.supply_risk_1h",
        (
            "fixture-event-copper-supply",
            "SUPPLY_REDUCTION",
            "fixture-entity-copper-mine",
            "copper",
            "fixture-market-copper-v1",
            "fixture-instrument-copper-v1",
            "fixture-contract-copper-v1",
        ),
    ),
    "crude_oil": (
        "intelligence.supply_risk_6h",
        (
            "fixture-event-crude-logistics",
            "TRANSPORT_DISRUPTION",
            "fixture-entity-crude-port",
            "crude_oil",
            "fixture-market-crude-oil-v1",
            "fixture-instrument-crude-oil-v1",
            "fixture-contract-crude-oil-v1",
        ),
    ),
    "gold": (
        "intelligence.geopolitical_risk",
        (
            "fixture-event-gold-risk",
            "RISK_PREMIUM_INCREASE",
            "fixture-entity-gold-country",
            "gold",
            "fixture-market-gold-v1",
            "fixture-instrument-gold-v1",
            "fixture-contract-gold-v1",
        ),
    ),
    "iron_ore": (
        "intelligence.demand_shock",
        (
            "fixture-event-iron-demand",
            "DEMAND_REDUCTION",
            "fixture-entity-iron-company",
            "iron_ore",
            "fixture-market-iron-ore-v1",
            "fixture-instrument-iron-ore-v1",
            "fixture-contract-iron-ore-v1",
        ),
    ),
    "soybean_meal": (
        "intelligence.inventory_stress",
        (
            "fixture-event-soybean-inventory",
            "INVENTORY_DRAW",
            "fixture-entity-soybean-port",
            "soybean_meal",
            "fixture-market-soybean-meal-v1",
            "fixture-instrument-soybean-meal-v1",
            "fixture-contract-soybean-meal-v1",
        ),
    ),
    "palm_oil": (
        "intelligence.supply_risk_24h",
        (
            "fixture-event-palm-weather",
            "SUPPLY_REDUCTION",
            "fixture-entity-palm-region",
            "palm_oil",
            "fixture-market-palm-oil-v1",
            "fixture-instrument-palm-oil-v1",
            "fixture-contract-palm-oil-v1",
        ),
    ),
}


def _canonical_event(*, case: FixtureCase, ontology):
    materialized = materialize_case_documents(case=case, ontology=ontology)
    current = None
    for document_key in case.event.evidence_document_keys:
        fixture_document = case.documents_by_key[document_key]
        _, candidate = materialized[document_key]
        current = merge_event(
            current=current,
            candidate=candidate,
            semantic_key=case.event.semantic_key,
            observed_at=fixture_document.observed_at,
            lifecycle=fixture_document.lifecycle,
        )
    assert current is not None
    return materialized, current


@pytest.mark.golden
@pytest.mark.e2e
def test_six_commodity_fixture_only_corpus_preserves_typed_lineage_and_feature_handoff():
    corpus = load_fixture_only_corpus(CORPUS_PATH)
    ontology = load_ontology(Path("ontology"))

    assert corpus.ontology_version == ontology.version
    assert {case.commodity_id for case in corpus.cases} == set(ontology.commodities)
    assert len(corpus.cases) == 6
    assert {case.commodity_id for case in corpus.cases} == set(_EXPECTED_FEATURE_DEFINITION_PATHS)
    available_feature_ids = {definition.feature_id for definition in INTELLIGENCE_FEATURE_DEFINITIONS}
    actual_event_hashes: dict[str, str] = {}
    actual_feature_handoff_hashes: dict[str, str] = {}

    for case in corpus.cases:
        materialized, canonical = _canonical_event(case=case, ontology=ontology)
        documents = tuple(document for document, _ in materialized.values())
        clusters = cluster_documents(
            documents=tuple(
                DedupDocument(document, case.documents_by_key[key].title, case.event.semantic_key)
                for key, (document, _) in materialized.items()
            )
        )
        assert len(clusters) == 1
        assert set(clusters[0].document_ids) == {document.document_id for document in documents}
        assert canonical.lifecycle is EventLifecycle.CONFIRMED
        assert len(canonical.extractions) == len(case.event.evidence_document_keys)
        assert {
            candidate.evidence.document_id for candidate in canonical.extractions
        } == {materialized[key][0].document_id for key in case.event.evidence_document_keys}

        resolver = EntityResolver(
            entities=(
                CanonicalEntity(
                    case.entity.entity_id,
                    case.entity.entity_type,
                    case.entity.canonical_name,
                    (case.entity.alias,),
                ),
            )
        )
        resolved = resolver.resolve(case.entity.alias)
        entity = case.entity.as_domain_entity()
        assert resolved.entity_id == entity.entity_id

        candidates_by_id = {
            candidate.extraction_id: candidate for _, candidate in materialized.values()
        }
        primary = candidates_by_id[case.event.primary_extraction_id]
        assessment = assess_mechanism(
            candidate=primary,
            mechanism_type=case.event.mechanism_type,
            ontology=ontology,
            rationale=case.event.rationale,
            confidence=case.event.assessment_confidence,
        )
        event = Event(
            case.event.event_id,
            ontology.version,
            tuple(candidate.evidence for candidate in canonical.extractions),
            assessment.domain_mechanism,
            (Impact(case.event.impact_id, case.commodity_id, case.event.direction),),
        )
        actual_event_hashes[case.commodity_id] = event.event_hash
        path = case.crosswalk.build_path(
            event=event,
            assessment=assessment,
            entity=entity,
            ontology=ontology,
        )
        assert tuple(node.node_id for node in path.nodes)[0] == event.event_id
        assert path.nodes[-1].node_id == case.crosswalk.contract.contract_id
        expected_feature_id, expected_node_ids = _EXPECTED_FEATURE_DEFINITION_PATHS[
            case.commodity_id
        ]
        assert case.crosswalk.feature_id == expected_feature_id
        assert tuple(node.node_id for node in path.nodes) == expected_node_ids

        assert case.crosswalk.feature_id in available_feature_ids
        registry = FeatureRegistry()
        feature_version = register_canonical_feature(
            registry,
            feature_id=case.crosswalk.feature_id,
            version="1.0.0",
            code_revision="p10-wp02-fixture-only-corpus",
        )
        assert registry.get_version(feature_version.version_hash) == feature_version
        assert feature_version.feature_id == case.crosswalk.feature_id
        handoff = FixtureOnlyFeatureDefinitionHandoff.from_lineage(
            case=case,
            canonical=canonical,
            event=event,
            path=path,
            feature_version=feature_version,
        )
        assert handoff.fixture_only is True
        assert handoff.research_only is True
        assert handoff.event_hash == event.event_hash
        assert handoff.feature_version_hash == feature_version.version_hash
        actual_feature_handoff_hashes[case.commodity_id] = handoff.handoff_hash

        assert not hasattr(event, "target_weight")
        assert not hasattr(path, "broker_order")
        assert not hasattr(feature_version, "execution_plan")
        assert not hasattr(handoff, "feature_value")
        assert not hasattr(handoff, "approval")
        assert not hasattr(handoff, "execution_plan")

    assert actual_event_hashes == {
        case.commodity_id: case.event.expected_event_hash for case in corpus.cases
    }
    assert actual_feature_handoff_hashes == {
        case.commodity_id: case.crosswalk.expected_feature_definition_handoff_hash
        for case in corpus.cases
    }
