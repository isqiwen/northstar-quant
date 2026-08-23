"""P2-WP07：研究状态不可由指标自动晋级。"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from northstar_quant.research.validation.research_decision import (
    HumanResearchApproval,
    ResearchDecision,
    ResearchDecisionError,
    ResearchDecisionEvidence,
    ResearchDecisionState,
)
import northstar_quant.research.validation.research_decision as decision_module


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _evidence(*, admission_status: str = "PASS") -> ResearchDecisionEvidence:
    return ResearchDecisionEvidence(
        experiment_spec_hash=_hash("experiment-spec"),
        experiment_run_hash=_hash("experiment-run"),
        backtest_result_hash=_hash("backtest-result"),
        validation_report_hash=_hash("validation-report"),
        admission_result_hash=_hash("admission-result"),
        admission_status=admission_status,
        _issuer=decision_module._EVIDENCE_ISSUER,
    )


def _approval(state: ResearchDecisionState) -> HumanResearchApproval:
    return HumanResearchApproval(
        approval_id=f"approval-{state.value}",
        approver_id="research-owner",
        approved_at=datetime(2026, 8, 21, 9, tzinfo=UTC),
        target_state=state,
        rationale="reviewed",
    )


def test_research_decision_requires_passed_evidence_and_named_human_approval() -> None:
    draft = ResearchDecision.draft(decision_id="trend-v1")
    research_only = draft.transition(target_state=ResearchDecisionState.RESEARCH_ONLY)

    with pytest.raises(ResearchDecisionError, match="人类批准"):
        research_only.transition(
            target_state=ResearchDecisionState.CANDIDATE,
            evidence=_evidence(),
        )
    with pytest.raises(ResearchDecisionError, match="完整研究准入"):
        research_only.transition(
            target_state=ResearchDecisionState.CANDIDATE,
            evidence=_evidence(admission_status="INSUFFICIENT_EVIDENCE"),
            approval=_approval(ResearchDecisionState.CANDIDATE),
        )

    candidate = research_only.transition(
        target_state=ResearchDecisionState.CANDIDATE,
        evidence=_evidence(),
        approval=_approval(ResearchDecisionState.CANDIDATE),
    )
    paper = candidate.transition(
        target_state=ResearchDecisionState.PAPER_ELIGIBLE,
        approval=_approval(ResearchDecisionState.PAPER_ELIGIBLE),
    )
    simulated = paper.transition(
        target_state=ResearchDecisionState.SIM_ELIGIBLE,
        approval=_approval(ResearchDecisionState.SIM_ELIGIBLE),
    )
    production_candidate = simulated.transition(
        target_state=ResearchDecisionState.PRODUCTION_CANDIDATE,
        approval=_approval(ResearchDecisionState.PRODUCTION_CANDIDATE),
    )

    assert production_candidate.eligible_for_trading is False
    assert production_candidate.predecessor_hash == simulated.decision_hash
    assert production_candidate.as_mapping()["eligible_for_trading"] is False


def test_research_decision_evidence_cannot_be_hand_declared_as_passed() -> None:
    with pytest.raises(ResearchDecisionError, match="只能由"):
        ResearchDecisionEvidence(
            experiment_spec_hash=_hash("experiment-spec"),
            experiment_run_hash=_hash("experiment-run"),
            backtest_result_hash=_hash("backtest-result"),
            validation_report_hash=_hash("validation-report"),
            admission_result_hash=_hash("admission-result"),
            admission_status="PASS",
        )


def test_rejected_is_terminal_and_transitions_are_forward_only() -> None:
    draft = ResearchDecision.draft(decision_id="trend-v1")
    rejected = draft.transition(target_state=ResearchDecisionState.REJECTED)

    with pytest.raises(ResearchDecisionError, match="不允许"):
        rejected.transition(target_state=ResearchDecisionState.RESEARCH_ONLY)
    with pytest.raises(ResearchDecisionError, match="不允许"):
        draft.transition(target_state=ResearchDecisionState.PAPER_ELIGIBLE)
