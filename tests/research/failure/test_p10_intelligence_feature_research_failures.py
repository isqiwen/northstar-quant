"""P10-WP03 failure paths preserve the fixture-only PIT and safety boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json

import pytest

from northstar_quant.research.intelligence_fixture_replay import (
    FixtureObservationLifecycle,
    FixtureOnlyReplayError,
    FixtureOnlyReplayRunner,
    FixtureReplayCheckpoint,
    FixtureReplayObservation,
)
from northstar_quant.research.validation.framework import (
    ResearchInputEvidenceKind,
    ResearchValidationEvidence,
    ValidationError,
)
from tests.helpers import p10_intelligence_feature_research as replay_helper
from tests.helpers.p10_intelligence_feature_research import (
    P10FixtureReplayError,
    build_p10_intelligence_feature_research_chain,
)
from tests.intelligence.golden._fixture_corpus import load_fixture_only_corpus


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def test_future_fixture_observation_and_outcome_fail_closed() -> None:
    chain = build_p10_intelligence_feature_research_chain()
    checkpoint = chain.plan.checkpoints[0]
    future_observation = replace(
        checkpoint.observations[0],
        available_at=checkpoint.decision_at + timedelta(seconds=1),
    )
    with pytest.raises(FixtureOnlyReplayError, match="PIT_OBSERVATION_NOT_AVAILABLE_AT_DECISION"):
        FixtureReplayCheckpoint.create(
            checkpoint_id=checkpoint.checkpoint_id,
            decision_at=checkpoint.decision_at,
            observations=(future_observation, *checkpoint.observations[1:]),
        )

    bad_outcome = replace(
        chain.plan.outcomes[0],
        available_at=checkpoint.decision_at,
    )
    with pytest.raises(FixtureOnlyReplayError, match="SYNTHETIC_OUTCOME_VISIBLE_AT_DECISION"):
        FixtureOnlyReplayRunner.create_plan(
            upstream_corpus_sha256=chain.plan.upstream_corpus_sha256,
            handoffs=chain.plan.handoffs,
            checkpoints=chain.plan.checkpoints,
            outcomes=(bad_outcome, *chain.plan.outcomes[1:]),
        )


def test_late_collected_copper_source_cannot_be_visible_before_collection() -> None:
    chain = build_p10_intelligence_feature_research_chain()
    corpus = load_fixture_only_corpus(replay_helper._CORPUS_PATH)
    copper = next(case for case in corpus.cases if case.commodity_id == "copper")
    late_collected_at = copper.documents_by_key["fixture-document-copper-late"].collected_at
    retained_available_at = max(
        copper.documents_by_key[key].collected_at
        for key in copper.event.evidence_document_keys
    )
    assert retained_available_at < late_collected_at

    original = chain.plan.checkpoints[0]
    early_decision = late_collected_at - timedelta(minutes=1)
    observations = []
    for observation in original.observations:
        observation_available_at = (
            late_collected_at if observation.commodity_id == "copper" else retained_available_at
        )
        observations.append(
            replace(
                observation,
                checkpoint_id="copper-before-late-collection",
                available_at=observation_available_at,
            )
        )
    with pytest.raises(FixtureOnlyReplayError, match="PIT_OBSERVATION_NOT_AVAILABLE_AT_DECISION"):
        FixtureReplayCheckpoint.create(
            checkpoint_id="copper-before-late-collection",
            decision_at=early_decision,
            observations=observations,
        )


def test_retracted_observation_cannot_retain_a_numeric_feature_score() -> None:
    chain = build_p10_intelligence_feature_research_chain()
    observation = next(
        item
        for checkpoint in chain.plan.checkpoints[12:]
        for item in checkpoint.observations
        if item.commodity_id == "gold"
    )
    assert observation.lifecycle is FixtureObservationLifecycle.RETRACTED
    with pytest.raises(FixtureOnlyReplayError, match="retracted observation"):
        FixtureReplayObservation(
            checkpoint_id=observation.checkpoint_id,
            commodity_id=observation.commodity_id,
            feature_id=observation.feature_id,
            feature_version_hash=observation.feature_version_hash,
            upstream_handoff_hash=observation.upstream_handoff_hash,
            available_at=observation.available_at,
            lifecycle=FixtureObservationLifecycle.RETRACTED,
            value=0.0,
            missing_reason="event_retracted",
        )


def test_handoff_feature_version_drift_and_dataset_injection_fail_closed() -> None:
    chain = build_p10_intelligence_feature_research_chain()
    drifted_handoff = replace(
        chain.plan.handoffs[0],
        feature_version_hash=_hash("different-feature-version"),
    )
    with pytest.raises(FixtureOnlyReplayError, match="feature_version_hash"):
        FixtureOnlyReplayRunner.create_plan(
            upstream_corpus_sha256=chain.plan.upstream_corpus_sha256,
            handoffs=(drifted_handoff, *chain.plan.handoffs[1:]),
            checkpoints=chain.plan.checkpoints,
            outcomes=chain.plan.outcomes,
        )
    with pytest.raises(ValidationError, match="cannot carry DatasetVersion"):
        ResearchValidationEvidence(
            dataset_version_hashes=(_hash("forbidden-p1-dataset"),),
            feature_version_hashes=chain.manifest.feature_version_hashes,
            strategy_version_hash=chain.manifest.methodology_version_hash,
            experiment_spec_hash=chain.manifest.experiment_spec_hash,
            experiment_run_hash=chain.manifest.experiment_run_hash,
            backtest_result_hash=chain.result.result_hash,
            input_kind=ResearchInputEvidenceKind.FIXTURE_ONLY_INTELLIGENCE_REPLAY,
            fixture_replay_binding_hash=chain.plan.plan_hash,
            code_revision=chain.manifest.code_revision,
        )


def test_companion_fixture_rejects_authority_or_unknown_p1_fields(tmp_path, monkeypatch) -> None:
    original = replay_helper._REPLAY_PATH.read_text(encoding="utf-8")
    payload = json.loads(original)
    payload["authority"]["authorized_market_data"] = True
    altered = tmp_path / "authority.json"
    altered.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(replay_helper, "_REPLAY_PATH", altered)
    with pytest.raises(P10FixtureReplayError, match="cannot claim"):
        replay_helper._load_fixture()

    unknown_payload = json.loads(original)
    unknown_payload["publication_authorization_hash"] = _hash("forbidden-p1-field")
    unknown = tmp_path / "unknown-p1-field.json"
    unknown.write_text(json.dumps(unknown_payload), encoding="utf-8")
    monkeypatch.setattr(replay_helper, "_REPLAY_PATH", unknown)
    with pytest.raises(P10FixtureReplayError, match="keys must be exact"):
        replay_helper._load_fixture()
