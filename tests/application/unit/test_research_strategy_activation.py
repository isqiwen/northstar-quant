"""P8-WP03 manual Research candidate-to-StrategyTarget activation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256

import pytest

from northstar_quant.application.research_strategy_activation import (
    HumanStrategyTargetActivationApproval,
    ResearchStrategyActivationError,
    ResearchStrategyActivationRequest,
    ResearchStrategyTargetActivator,
    StrategyTargetProposal,
)
from northstar_quant.research.validation.research_decision import (
    HumanResearchApproval,
    ResearchDecisionState,
)
from tests.helpers.research_candidate import (
    ResearchCandidateChain,
    build_research_candidate_chain,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _proposal(chain: ResearchCandidateChain, **overrides: object) -> StrategyTargetProposal:
    candidate_approval = chain.card.decision.approval
    assert candidate_approval is not None
    generated_at = candidate_approval.approved_at + timedelta(minutes=1)
    values: dict[str, object] = {
        "target_id": "p8-research-target",
        "source_strategy_id": chain.strategy.strategy_id,
        "source_strategy_version": chain.strategy.version,
        "generated_at": generated_at,
        "effective_at": generated_at + timedelta(minutes=2),
        "expires_at": generated_at + timedelta(hours=1),
        "positions": (),
    }
    values.update(overrides)
    if not values["positions"]:
        from northstar_quant.portfolio_risk.portfolio import TargetPosition

        values["positions"] = (TargetPosition("SHFE.RB2610", 0.1),)
    return StrategyTargetProposal(**values)  # type: ignore[arg-type]


def _approval(
    chain: ResearchCandidateChain,
    proposal: StrategyTargetProposal,
    **overrides: object,
) -> HumanStrategyTargetActivationApproval:
    values: dict[str, object] = {
        "activation_id": "p8-manual-activation",
        "approver_id": "strategy-owner",
        "approved_at": proposal.generated_at + timedelta(minutes=1),
        "target_proposal_hash": proposal.proposal_hash,
        "research_card_hash": chain.card.card_hash,
        "research_decision_hash": chain.card.decision.decision_hash,
        "experiment_spec_hash": chain.experiment.spec_hash,
        "strategy_version_hash": chain.strategy.reference_hash,
        "rationale": "target-review-complete",
    }
    values.update(overrides)
    return HumanStrategyTargetActivationApproval(**values)  # type: ignore[arg-type]


def _request(
    chain: ResearchCandidateChain,
    *,
    proposal: StrategyTargetProposal | None = None,
    approval: HumanStrategyTargetActivationApproval | None = None,
    experiment_run=None,
) -> ResearchStrategyActivationRequest:
    proposal = proposal or _proposal(chain)
    approval = approval or _approval(chain, proposal)
    return ResearchStrategyActivationRequest(
        research_card=chain.card,
        experiment_spec=chain.experiment,
        experiment_run=chain.experiment_run if experiment_run is None else experiment_run,
        target_proposal=proposal,
        activation_approval=approval,
    )


def test_manual_activation_preserves_the_full_research_chain_and_never_trades(tmp_path) -> None:
    chain = build_research_candidate_chain(tmp_path / "candidate")

    receipt = ResearchStrategyTargetActivator().activate(_request(chain))

    evidence = chain.card.validation_report.evidence
    target = receipt.strategy_target
    assert receipt.eligible_for_trading is False
    assert target.activation.activation_hash == receipt.activation_hash
    assert target.activation.activation_id == receipt.activation_approval.activation_id
    assert target.generated_at == receipt.target_proposal.generated_at
    assert target.as_mapping()["format"] == "northstar.strategy-target.v2"
    assert receipt.research_card_id == chain.card.card_id
    assert receipt.research_card_hash == chain.card.card_hash
    assert receipt.research_decision_id == chain.card.decision.decision_id
    assert receipt.research_decision_hash == chain.card.decision.decision_hash
    assert receipt.research_decision_evidence_hash == chain.card.decision.evidence.evidence_hash
    assert receipt.research_approval_hash == chain.card.decision.approval.approval_hash
    assert receipt.research_approved_at == chain.card.decision.approval.approved_at
    assert receipt.validation_report_hash == chain.card.validation_report.report_hash
    assert receipt.dataset_version_hashes == evidence.dataset_version_hashes
    assert receipt.feature_version_hashes == evidence.feature_version_hashes
    assert receipt.experiment_spec_hash == chain.experiment.spec_hash
    assert receipt.experiment_run_hash == chain.experiment_run.run_hash
    assert receipt.backtest_result_hash == chain.card.run_manifest.result.result_hash
    assert receipt.strategy_version_hash == chain.strategy.reference_hash
    assert receipt.strategy_spec_hash == chain.strategy.spec_hash
    assert receipt.strategy_implementation_hash == chain.strategy.implementation_hash
    assert receipt.strategy_code_revision == chain.strategy.code_revision
    assert receipt.input_as_of == chain.experiment.input_as_of
    assert receipt.selection_mode == "STATIC_REPRODUCIBILITY_ONLY"
    assert receipt.decision_time_safe is False
    assert receipt.as_mapping()["eligible_for_trading"] is False
    assert receipt.as_mapping()["research"]["candidate_approved_at"] == (
        chain.card.decision.approval.approved_at.isoformat()
    )
    assert "order" not in receipt.as_mapping()


def test_same_research_and_manual_inputs_produce_the_same_activation_receipt(tmp_path) -> None:
    first_chain = build_research_candidate_chain(tmp_path / "first")
    second_chain = build_research_candidate_chain(tmp_path / "second")

    first = ResearchStrategyTargetActivator().activate(_request(first_chain))
    second = ResearchStrategyTargetActivator().activate(_request(second_chain))

    assert first.activation_hash == second.activation_hash
    assert first.strategy_target.target_hash == second.strategy_target.target_hash
    assert first.as_mapping() == second.as_mapping()


@pytest.mark.parametrize(
    "case",
    (
        "wrong-manual-binding",
        "wrong-card-binding",
        "wrong-decision-binding",
        "wrong-experiment-binding",
        "wrong-strategy-binding",
        "wrong-proposal-strategy",
        "proposal-before-candidate-approval",
        "activation-at-effective",
        "wrong-experiment-run",
    ),
)
def test_activation_fails_closed_for_mismatched_manual_research_and_target_inputs(
    tmp_path,
    case: str,
) -> None:
    chain = build_research_candidate_chain(tmp_path / case)
    proposal = _proposal(chain)

    if case == "wrong-manual-binding":
        request = _request(
            chain,
            proposal=proposal,
            approval=_approval(chain, proposal, target_proposal_hash=_hash("other-proposal")),
        )
        match = "manual approval does not bind"
    elif case == "wrong-card-binding":
        request = _request(
            chain,
            proposal=proposal,
            approval=_approval(chain, proposal, research_card_hash=_hash("other-card")),
        )
        match = "manual approval does not bind"
    elif case == "wrong-decision-binding":
        request = _request(
            chain,
            proposal=proposal,
            approval=_approval(chain, proposal, research_decision_hash=_hash("other-decision")),
        )
        match = "manual approval does not bind"
    elif case == "wrong-experiment-binding":
        request = _request(
            chain,
            proposal=proposal,
            approval=_approval(chain, proposal, experiment_spec_hash=_hash("other-experiment")),
        )
        match = "manual approval does not bind"
    elif case == "wrong-strategy-binding":
        request = _request(
            chain,
            proposal=proposal,
            approval=_approval(chain, proposal, strategy_version_hash=_hash("other-strategy")),
        )
        match = "manual approval does not bind"
    elif case == "wrong-proposal-strategy":
        mismatched = _proposal(chain, source_strategy_version="2.0.0")
        request = _request(chain, proposal=mismatched, approval=_approval(chain, mismatched))
        match = "target proposal must match"
    elif case == "proposal-before-candidate-approval":
        candidate_approval = chain.card.decision.approval
        assert candidate_approval is not None
        early = _proposal(
            chain,
            generated_at=candidate_approval.approved_at - timedelta(minutes=1),
            effective_at=candidate_approval.approved_at + timedelta(minutes=2),
            expires_at=candidate_approval.approved_at + timedelta(hours=1),
        )
        request = _request(
            chain,
            proposal=early,
            approval=_approval(chain, early, approved_at=candidate_approval.approved_at + timedelta(minutes=1)),
        )
        match = "timestamps violate"
    elif case == "activation-at-effective":
        request = _request(
            chain,
            proposal=proposal,
            approval=_approval(chain, proposal, approved_at=proposal.effective_at),
        )
        match = "timestamps violate"
    else:
        mismatched_run = replace(chain.experiment_run, spec_hash=_hash("other-experiment"))
        request = _request(chain, proposal=proposal, experiment_run=mismatched_run)
        match = "experiment spec/run"

    with pytest.raises(ResearchStrategyActivationError, match=match):
        ResearchStrategyTargetActivator().activate(request)


def test_activation_rejects_non_candidate_and_attempted_pit_promotion(tmp_path) -> None:
    candidate_chain = build_research_candidate_chain(tmp_path / "non-candidate")
    paper_decision = candidate_chain.card.decision.transition(
        target_state=ResearchDecisionState.PAPER_ELIGIBLE,
        approval=HumanResearchApproval(
            approval_id="paper-approval",
            approver_id="research-owner",
            approved_at=candidate_chain.card.decision.approval.approved_at + timedelta(minutes=1),
            target_state=ResearchDecisionState.PAPER_ELIGIBLE,
            rationale="paper-reviewed",
        ),
    )
    paper_card = type(candidate_chain.card).create(
        card_id=candidate_chain.card.card_id,
        run_manifest=candidate_chain.card.run_manifest,
        validation_report=candidate_chain.card.validation_report,
        decision=paper_decision,
        product_contributions=candidate_chain.card.product_contributions,
        limitations=candidate_chain.card.limitations,
    )
    candidate_chain = replace(candidate_chain, card=paper_card)

    with pytest.raises(ResearchStrategyActivationError, match="exactly CANDIDATE"):
        ResearchStrategyTargetActivator().activate(_request(candidate_chain))

    pit_chain = build_research_candidate_chain(tmp_path / "pit-promotion")
    object.__setattr__(pit_chain.experiment, "decision_time_safe", True)

    with pytest.raises(ResearchStrategyActivationError, match="cannot claim decision-safe"):
        ResearchStrategyTargetActivator().activate(_request(pit_chain))


def test_activation_rejects_ordinary_receipt_reconstruction_and_unknown_request_types(tmp_path) -> None:
    chain = build_research_candidate_chain(tmp_path / "receipt")
    receipt = ResearchStrategyTargetActivator().activate(_request(chain))

    with pytest.raises(ResearchStrategyActivationError, match="only be issued"):
        replace(receipt)
    with pytest.raises(ResearchStrategyActivationError, match="ResearchStrategyActivationRequest"):
        ResearchStrategyTargetActivator().activate(object())  # type: ignore[arg-type]
    assert receipt.eligible_for_trading is False
