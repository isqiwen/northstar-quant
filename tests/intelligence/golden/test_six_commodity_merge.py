"""P10 golden lifecycle corpus for source merge, retraction and late evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from northstar_quant.intelligence.event_merge import EventLifecycle, merge_event
from northstar_quant.intelligence.ontology import load_ontology
from tests.intelligence.golden._fixture_corpus import (
    load_fixture_only_corpus,
    materialize_case_documents,
)


CORPUS_PATH = Path(__file__).with_name("six_commodity_fixture_only_v1.json")


@pytest.mark.golden
def test_fixture_lifecycle_corpus_preserves_all_source_lineage_without_reactivation():
    corpus = load_fixture_only_corpus(CORPUS_PATH)
    ontology = load_ontology(Path("ontology"))
    seen_lifecycles: set[EventLifecycle] = set()
    final_events = {}

    for scenario in corpus.merge_scenarios:
        case = corpus.cases_by_id[scenario.case_id]
        materialized = materialize_case_documents(case=case, ontology=ontology)
        current = None
        for step in scenario.steps:
            _, candidate = materialized[step.document_key]
            previous = current
            current = merge_event(
                current=current,
                candidate=candidate,
                semantic_key=scenario.semantic_key,
                observed_at=step.observed_at,
                lifecycle=step.lifecycle,
            )
            if previous is not None and candidate.extraction_id in previous.extraction_ids:
                assert current is previous
            assert current.lifecycle is step.expected_lifecycle
            assert current.observed_at == step.expected_observed_at
            assert current.extraction_ids == step.expected_extraction_ids
            assert tuple(item.extraction_id for item in current.extractions) == current.extraction_ids
            seen_lifecycles.add(step.lifecycle)
        final_events[scenario.scenario_id] = current

    assert seen_lifecycles == set(EventLifecycle)
    gold_retraction = final_events["fixture-merge-gold"]
    assert gold_retraction.lifecycle is EventLifecycle.RETRACTED

    gold_case = corpus.cases_by_id["fixture-case-gold"]
    gold_materialized = materialize_case_documents(case=gold_case, ontology=ontology)
    _, original = gold_materialized["fixture-document-gold-primary"]
    reactivation = replace(original, extraction_id="fixture-extraction-gold-reactivation")
    with pytest.raises(ValueError, match="not permitted"):
        merge_event(
            current=gold_retraction,
            candidate=reactivation,
            semantic_key=gold_case.event.semantic_key,
            observed_at=gold_retraction.observed_at.replace(minute=21),
            lifecycle=EventLifecycle.OPEN,
        )
