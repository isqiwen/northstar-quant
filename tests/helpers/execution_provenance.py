"""Reusable P10-WP05 CTP-sim-only provenance fixture.

The helper starts from two independent real P2 candidates, reaches P3 through
two named manual activation receipts, composes them canonically, and then
derives the portfolio-wide risk review and approval through the P3 gate.
It supplies explicit current account, quote, and contract-rule evidence, but
never connects to a broker or submits an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

from northstar_quant.application.execution_provenance_preflight import (
    AccountAttributionEvidence,
    ExecutionContractRuleEvidence,
    ExecutionDataEvidence,
    ExecutionProvenanceEnvironment,
    ExecutionProvenanceRequest,
)
from northstar_quant.application.portfolio_risk_authority import (
    PortfolioRiskAuthorityResolver,
    ReconciliationSafetyStateEvidence,
)
from northstar_quant.application.research_strategy_activation import (
    HumanStrategyTargetActivationApproval,
    ResearchStrategyActivationReceipt,
    ResearchStrategyActivationRequest,
    ResearchStrategyTargetActivator,
    StrategyTargetProposal,
)
from northstar_quant.platform.config.settings import Settings, get_settings
from northstar_quant.platform.config.trading_profile import TradingProfile, load_trading_profile
from northstar_quant.portfolio_risk.allocation import (
    AllocationPolicy,
    StrategyAllocationInput,
)
from northstar_quant.portfolio_risk.portfolio import (
    CanonicalPortfolioComposer,
    PortfolioCompositionEvidence,
    PortfolioCompositionRequest,
    PortfolioRiskApprovalEvidence,
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
    RiskApprovalAttestation,
    TargetPosition,
)
from northstar_quant.portfolio_risk.risk import RiskStateSnapshot
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    FuturesExecutionRule,
    MarketQuoteSnapshot,
)
from tests.helpers.research_candidate import ResearchCandidateChain, build_research_candidate_chain


@dataclass(frozen=True, slots=True)
class ExecutionProvenanceFixture:
    """Exact P2 → P3 → P5 evidence inputs for P8 tests."""

    request: ExecutionProvenanceRequest
    chains: tuple[ResearchCandidateChain, ResearchCandidateChain]
    activation_requests: tuple[ResearchStrategyActivationRequest, ResearchStrategyActivationRequest]
    activation_receipts: tuple[ResearchStrategyActivationReceipt, ResearchStrategyActivationReceipt]
    composition_evidence: PortfolioCompositionEvidence
    portfolio_risk_approval_request: PortfolioRiskApprovalRequest
    portfolio_risk_approval_evidence: PortfolioRiskApprovalEvidence
    profile: TradingProfile
    settings: Settings


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _activation(
    chain: ResearchCandidateChain,
    *,
    target_id: str,
    activation_id: str,
    generated_at: datetime | None = None,
) -> tuple[ResearchStrategyActivationRequest, ResearchStrategyActivationReceipt]:
    candidate_approval = chain.card.decision.approval
    assert candidate_approval is not None
    generated_at = generated_at or (
        candidate_approval.approved_at + timedelta(minutes=1)
    )
    proposal = StrategyTargetProposal(
        target_id=target_id,
        source_strategy_id=chain.strategy.strategy_id,
        source_strategy_version=chain.strategy.version,
        generated_at=generated_at,
        effective_at=generated_at + timedelta(minutes=2),
        expires_at=generated_at + timedelta(hours=1),
        positions=(TargetPosition("RB2610", 0.1),),
    )
    approval = HumanStrategyTargetActivationApproval(
        activation_id=activation_id,
        approver_id="strategy-owner",
        approved_at=generated_at + timedelta(minutes=1),
        target_proposal_hash=proposal.proposal_hash,
        research_card_hash=chain.card.card_hash,
        research_decision_hash=chain.card.decision.decision_hash,
        experiment_spec_hash=chain.experiment.spec_hash,
        strategy_version_hash=chain.strategy.reference_hash,
        rationale="ctp-sim candidate target reviewed",
    )
    request = ResearchStrategyActivationRequest(
        research_card=chain.card,
        experiment_spec=chain.experiment,
        experiment_run=chain.experiment_run,
        target_proposal=proposal,
        activation_approval=approval,
    )
    return request, ResearchStrategyTargetActivator().activate(request)


def build_execution_provenance_fixture(
    root: Path,
    *,
    broker_state: BrokerStateSnapshot | None = None,
    reconciliation_safety_state: ReconciliationSafetyStateEvidence | None = None,
    reviewed_at: datetime | None = None,
) -> ExecutionProvenanceFixture:
    """Build an exact, time-ordered canonical CTP-sim evidence chain for tests."""

    first_chain = build_research_candidate_chain(root / "trend")
    second_chain = build_research_candidate_chain(
        root / "carry",
        strategy_id="futures.carry",
    )
    requested_reviewed_at = reviewed_at
    activation_generated_at = (
        requested_reviewed_at - timedelta(minutes=4)
        if requested_reviewed_at is not None
        else None
    )
    first_request, first_receipt = _activation(
        first_chain,
        target_id="p8-rb2610-trend-target",
        activation_id="p8-rb2610-trend-activation",
        generated_at=activation_generated_at,
    )
    second_request, second_receipt = _activation(
        second_chain,
        target_id="p8-rb2610-carry-target",
        activation_id="p8-rb2610-carry-activation",
        generated_at=activation_generated_at,
    )
    activation_requests = (first_request, second_request)
    activation_receipts = (first_receipt, second_receipt)
    generated_at = first_request.target_proposal.generated_at
    composition_request = PortfolioCompositionRequest(
        target_id="p8-rb2610-canonical-portfolio",
        generated_at=generated_at + timedelta(minutes=3),
        effective_at=generated_at + timedelta(minutes=4),
        expires_at=first_request.target_proposal.expires_at,
        allocation_policy=AllocationPolicy(cash_reserve=0.0, target_volatility=0.1),
        allocation_inputs=tuple(
            StrategyAllocationInput(
                strategy_target=receipt.strategy_target,
                fixed_budget=0.5,
                realized_volatility=0.1,
                risk_budget=1.0,
                max_allocation=0.5,
            )
            for receipt in activation_receipts
        ),
    )
    composition_evidence = CanonicalPortfolioComposer().compose(composition_request)
    reviewed_at = reviewed_at or composition_request.effective_at
    profile = load_trading_profile("cn_futures_daily_trend_simulated")
    settings = get_settings().model_copy(
        update={
            "broker": "ctp_sim",
            "live_trading_enabled": False,
            "kill_switch_enabled": False,
        }
    )
    snapshot = broker_state or BrokerStateSnapshot(
        account="ctp-sim-test",
        state_complete=True,
        asof=reviewed_at,
        account_values={
            "Balance": 100_000.0,
            "NetLiquidation": 100_000.0,
            "Available": 90_000.0,
            "AvailableFunds": 90_000.0,
            "CurrMargin": 0.0,
        },
    )
    if reconciliation_safety_state is None:
        normal_safety_snapshot = RiskStateSnapshot.initial(occurred_at=reviewed_at)
        reconciliation_safety_state = ReconciliationSafetyStateEvidence(
            profile_id=profile.profile_id,
            broker="ctp_sim",
            account_id="ctp-sim-test",
            state_snapshot=normal_safety_snapshot,
            reconciliation_state_hash=normal_safety_snapshot.state_hash,
        )
    authority = PortfolioRiskAuthorityResolver().resolve(
        profile=profile,
        broker_state=snapshot,
        reconciliation_safety_state=reconciliation_safety_state,
        composition=composition_evidence,
        evaluated_at=reviewed_at,
    )
    risk_gate = PortfolioRiskApprovalGate()
    review = risk_gate.review(authority.review_request)
    approval_request = PortfolioRiskApprovalRequest(
        review_request=authority.review_request,
        attestation=RiskApprovalAttestation(
            approval_id="p8-rb2610-risk-approval",
            review_hash=review.review_hash,
            approver_id="risk-owner",
            approved_at=reviewed_at,
            rationale="canonical portfolio review approved for ctp-sim evidence",
        ),
    )
    approval_evidence = risk_gate.evaluate(approval_request)
    assert approval_evidence.approved_target is not None
    quote = MarketQuoteSnapshot(
        symbol="RB2610",
        bid=3099.0,
        ask=3101.0,
        last=3100.0,
        market_price=3100.0,
        asof=reviewed_at,
        source="ctp_sim_market_data",
    )
    authority_rule = authority.execution_rules[0]
    rule = ExecutionContractRuleEvidence(
        symbol=authority_rule.symbol,
        instrument_id=authority_rule.instrument_id,
        exchange_id=authority_rule.exchange_id,
        volume_multiple=authority_rule.volume_multiple,
        rule=FuturesExecutionRule(
            margin_rate=authority_rule.margin_rate,
            max_position_lots=authority_rule.max_position_lots,
        ),
        available_at=generated_at,
        effective_at=first_request.target_proposal.effective_at,
        expires_at=first_request.target_proposal.expires_at,
    )
    data_evidence = ExecutionDataEvidence(
        profile_id=profile.profile_id,
        dataset_id=profile.data.dataset_id,
        data_source="akshare_actual_daily",
        content_sha256=_hash("p8-ctp-sim-runtime-data"),
        raw_market_as_of=reviewed_at,
        signal_market_as_of=reviewed_at,
        target_output_as_of=reviewed_at,
    )
    request = ExecutionProvenanceRequest(
        preflight_id="p8-rb2610-preflight",
        environment=ExecutionProvenanceEnvironment.CTP_SIM,
        profile=profile,
        settings=settings,
        activation_requests=activation_requests,
        activation_receipts=activation_receipts,
        portfolio_risk_approval_request=approval_request,
        portfolio_risk_approval_evidence=approval_evidence,
        portfolio_risk_authority=authority,
        reconciliation_safety_state=reconciliation_safety_state,
        data_evidence=data_evidence,
        account_snapshot=snapshot,
        account_attribution=AccountAttributionEvidence(
            account="ctp-sim-test",
            observed_at=reviewed_at,
        ),
        quotes=(quote,),
        contract_rules=(rule,),
        plan_id="p8-rb2610-plan",
        market_snapshot_at=reviewed_at,
        plan_created_at=reviewed_at,
        checked_at=reviewed_at,
    )
    return ExecutionProvenanceFixture(
        request=request,
        chains=(first_chain, second_chain),
        activation_requests=activation_requests,
        activation_receipts=activation_receipts,
        composition_evidence=composition_evidence,
        portfolio_risk_approval_request=approval_request,
        portfolio_risk_approval_evidence=approval_evidence,
        profile=profile,
        settings=settings,
    )


__all__ = ["ExecutionProvenanceFixture", "build_execution_provenance_fixture"]
