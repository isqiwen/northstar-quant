"""Failure-closed contract for the P10 fixture-only intelligence corpus."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from northstar_quant.intelligence.domain import Event, Impact
from northstar_quant.intelligence.event_merge import merge_event
from northstar_quant.intelligence.impact_graph import ContractRef
from northstar_quant.intelligence.mechanisms import assess_mechanism
from northstar_quant.intelligence.ontology import OntologyError, load_ontology
from tests.intelligence.golden._fixture_corpus import (
    FixtureCorpusError,
    load_fixture_only_corpus,
    materialize_case_documents,
)


CORPUS_PATH = Path("tests/intelligence/golden/six_commodity_fixture_only_v1.json")


def _payload() -> dict[str, object]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _write_payload(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "mutated-fixture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.contract
def test_fixture_loader_rejects_authority_claims_hash_drift_url_drift_and_schema_drift(tmp_path):
    authority_payload = _payload()
    authority = authority_payload["authority"]
    assert isinstance(authority, dict)
    authority["authorized_market_data"] = True
    with pytest.raises(FixtureCorpusError, match="authority flags"):
        load_fixture_only_corpus(_write_payload(tmp_path, authority_payload))

    hash_payload = _payload()
    cases = hash_payload["commodity_cases"]
    assert isinstance(cases, list)
    first_case = cases[0]
    assert isinstance(first_case, dict)
    documents = first_case["documents"]
    assert isinstance(documents, list)
    first_document = documents[0]
    assert isinstance(first_document, dict)
    first_document["content_sha256"] = "0" * 64
    with pytest.raises(FixtureCorpusError, match="content_sha256"):
        load_fixture_only_corpus(_write_payload(tmp_path, hash_payload))

    url_payload = _payload()
    url_cases = url_payload["commodity_cases"]
    assert isinstance(url_cases, list)
    url_case = url_cases[0]
    assert isinstance(url_case, dict)
    url_documents = url_case["documents"]
    assert isinstance(url_documents, list)
    url_document = url_documents[0]
    assert isinstance(url_document, dict)
    url_document["canonical_url"] = "https://example.test/not-fixture"
    with pytest.raises(FixtureCorpusError, match="fixture.invalid"):
        load_fixture_only_corpus(_write_payload(tmp_path, url_payload))

    schema_payload = _payload()
    schema_cases = schema_payload["commodity_cases"]
    assert isinstance(schema_cases, list)
    schema_case = schema_cases[0]
    assert isinstance(schema_case, dict)
    crosswalk = schema_case["crosswalk"]
    assert isinstance(crosswalk, dict)
    crosswalk.pop("contract")
    with pytest.raises(FixtureCorpusError, match="crosswalk keys"):
        load_fixture_only_corpus(_write_payload(tmp_path, schema_payload))

    direction_payload = _payload()
    direction_cases = direction_payload["commodity_cases"]
    assert isinstance(direction_cases, list)
    direction_case = direction_cases[0]
    assert isinstance(direction_case, dict)
    direction_event = direction_case["event"]
    assert isinstance(direction_event, dict)
    impact = direction_event["impact"]
    assert isinstance(impact, dict)
    impact["direction"] = "SIDEWAYS"
    with pytest.raises(FixtureCorpusError, match="supported direction"):
        load_fixture_only_corpus(_write_payload(tmp_path, direction_payload))


@pytest.mark.contract
def test_fixture_loader_rejects_unknown_event_type_before_any_feature_handoff(tmp_path):
    payload = _payload()
    cases = payload["commodity_cases"]
    assert isinstance(cases, list)
    first_case = cases[0]
    assert isinstance(first_case, dict)
    event = first_case["event"]
    assert isinstance(event, dict)
    event["event_type"] = "UNKNOWN"
    corpus = load_fixture_only_corpus(_write_payload(tmp_path, payload))
    ontology = load_ontology(Path("ontology"))

    with pytest.raises(OntologyError, match="unknown event type"):
        materialize_case_documents(case=corpus.cases[0], ontology=ontology)


@pytest.mark.contract
def test_fixture_loader_rejects_primary_mechanism_evidence_outside_event_lineage(tmp_path):
    payload = _payload()
    cases = payload["commodity_cases"]
    assert isinstance(cases, list)
    first_case = cases[0]
    assert isinstance(first_case, dict)
    event = first_case["event"]
    assert isinstance(event, dict)
    documents = first_case["documents"]
    assert isinstance(documents, list)
    late_document = documents[-1]
    assert isinstance(late_document, dict)
    late_extraction = late_document["extraction"]
    assert isinstance(late_extraction, dict)
    event["primary_extraction_id"] = late_extraction["extraction_id"]

    with pytest.raises(FixtureCorpusError, match="backed by event evidence"):
        load_fixture_only_corpus(_write_payload(tmp_path, payload))


@pytest.mark.contract
def test_fixture_crosswalk_rejects_inconsistent_contract_mapping_without_broker_semantics():
    corpus = load_fixture_only_corpus(CORPUS_PATH)
    ontology = load_ontology(Path("ontology"))
    case = corpus.cases_by_id["fixture-case-copper"]
    materialized = materialize_case_documents(case=case, ontology=ontology)
    current = None
    for document_key in case.event.evidence_document_keys:
        fixture = case.documents_by_key[document_key]
        _, candidate = materialized[document_key]
        current = merge_event(
            current=current,
            candidate=candidate,
            semantic_key=case.event.semantic_key,
            observed_at=fixture.observed_at,
            lifecycle=fixture.lifecycle,
        )
    assert current is not None
    candidates = {candidate.extraction_id: candidate for _, candidate in materialized.values()}
    assessment = assess_mechanism(
        candidate=candidates[case.event.primary_extraction_id],
        mechanism_type=case.event.mechanism_type,
        ontology=ontology,
        rationale=case.event.rationale,
        confidence=case.event.assessment_confidence,
    )
    event = Event(
        case.event.event_id,
        ontology.version,
        tuple(candidate.evidence for candidate in current.extractions),
        assessment.domain_mechanism,
        (Impact(case.event.impact_id, case.commodity_id, case.event.direction),),
    )
    _, late_candidate = materialized["fixture-document-copper-late"]
    late_assessment = assess_mechanism(
        candidate=late_candidate,
        mechanism_type=case.event.mechanism_type,
        ontology=ontology,
        rationale=case.event.rationale,
        confidence=case.event.assessment_confidence,
    )
    with pytest.raises(FixtureCorpusError, match="assessment evidence must be retained"):
        case.crosswalk.build_path(
            event=event,
            assessment=late_assessment,
            entity=case.entity.as_domain_entity(),
            ontology=ontology,
        )
    mismatched = replace(
        case.crosswalk,
        contract=ContractRef(
            "fixture-contract-copper-other-v1",
            "fixture-instrument-copper-other-v1",
            case.commodity_id,
        ),
    )

    with pytest.raises(FixtureCorpusError, match="inconsistent"):
        mismatched.build_path(
            event=event,
            assessment=assessment,
            entity=case.entity.as_domain_entity(),
            ontology=ontology,
        )
    assert "trading_execution" not in type(mismatched).__module__
