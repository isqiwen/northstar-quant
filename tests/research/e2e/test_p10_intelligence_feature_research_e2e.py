"""P10-WP03: six WP02 fixture handoffs form a research-only PIT evidence chain."""

from __future__ import annotations

import pytest

from northstar_quant.application.research_strategy_activation import (
    ResearchStrategyActivationError,
    _replay_card_and_validation,
)
from northstar_quant.research.validation.framework import ResearchInputEvidenceKind
from northstar_quant.research.validation.research_decision import (
    ResearchDecisionError,
    ResearchDecisionState,
)
from tests.helpers.p10_intelligence_feature_research import (
    build_p10_intelligence_feature_research_chain,
)


@pytest.mark.e2e
@pytest.mark.golden
def test_six_commodity_fixture_handoffs_replay_to_research_only_card_deterministically() -> None:
    first = build_p10_intelligence_feature_research_chain()
    repeated = build_p10_intelligence_feature_research_chain()

    assert {item.commodity_id for item in first.plan.handoffs} == {
        "copper",
        "crude_oil",
        "gold",
        "iron_ore",
        "soybean_meal",
        "palm_oil",
    }
    assert first.plan.plan_hash == repeated.plan.plan_hash
    assert first.result.result_hash == repeated.result.result_hash
    assert first.manifest.run_fingerprint == repeated.manifest.run_fingerprint
    assert first.validation.report_hash == repeated.validation.report_hash
    assert first.card.card_hash == repeated.card.card_hash
    assert first.card.to_json() == repeated.card.to_json()

    assert first.plan.fixture_only is True
    assert first.plan.research_only is True
    assert first.plan.eligible_for_admission is False
    assert first.plan.eligible_for_trading is False
    assert all(
        observation.available_at <= checkpoint.decision_at
        for checkpoint in first.plan.checkpoints
        for observation in checkpoint.observations
    )
    assert all(
        outcome.available_at > checkpoint.decision_at
        for checkpoint in first.plan.checkpoints
        for outcome in first.plan.outcomes
        if outcome.checkpoint_id == checkpoint.checkpoint_id
    )

    retracted_gold = [
        observation
        for checkpoint in first.plan.checkpoints[12:]
        for observation in checkpoint.observations
        if observation.commodity_id == "gold"
    ]
    assert retracted_gold
    assert all(
        observation.lifecycle.value == "retracted"
        and observation.value is None
        and observation.missing_reason == "event_retracted"
        for observation in retracted_gold
    )

    assert first.result.models_orders is False
    assert first.result.models_trades is False
    assert first.result.order_count == 0
    assert first.result.trade_count == 0
    assert first.result.eligible_for_admission is False
    assert first.result.eligible_for_trading is False
    assert first.validation.evidence.input_kind is ResearchInputEvidenceKind.FIXTURE_ONLY_INTELLIGENCE_REPLAY
    assert first.validation.evidence.dataset_version_hashes == ()
    assert first.validation.evidence.fixture_replay_binding_hash == first.plan.plan_hash
    assert first.validation.eligible_for_admission is False

    assert first.decision.state is ResearchDecisionState.RESEARCH_ONLY
    assert first.decision.evidence is None
    assert first.decision.approval is None
    assert first.card.eligible_for_trading is False
    assert first.card.as_mapping()["execution_assumptions"] == {"not_applicable": True}


def test_fixture_only_chain_cannot_be_promoted_to_candidate_or_strategy_target() -> None:
    chain = build_p10_intelligence_feature_research_chain()

    with pytest.raises(ResearchDecisionError):
        chain.decision.transition(target_state=ResearchDecisionState.CANDIDATE)
    with pytest.raises(
        ResearchStrategyActivationError,
        match="fixture-only intelligence replay evidence cannot activate",
    ):
        _replay_card_and_validation(chain.card)
