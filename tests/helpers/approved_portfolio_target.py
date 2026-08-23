"""Fixture-only canonical P3 approval for generic portfolio/execution tests.

This helper intentionally uses the public two-source composition and
``PortfolioRiskApprovalGate`` contracts.  It is not market, broker, account,
or production-authority data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from northstar_quant.portfolio_risk.allocation import (
    AllocationPolicy,
    StrategyAllocationInput,
)
from northstar_quant.portfolio_risk.portfolio import (
    AccountScopedRiskStateEvidence,
    ApprovedPortfolioTarget,
    CanonicalPortfolioComposer,
    PortfolioCompositionRequest,
    PortfolioRiskAccountSnapshot,
    PortfolioRiskApprovalEvidence,
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
    PortfolioRiskInstrumentSnapshot,
    PortfolioRiskReview,
    PortfolioRiskReviewRequest,
    RiskApprovalAttestation,
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
)
from northstar_quant.portfolio_risk.risk import RiskStateSnapshot
from tests.helpers.canonical_portfolio_risk import build_portfolio_risk_policy


@dataclass(frozen=True, slots=True)
class ApprovedPortfolioTargetFixture:
    """One non-authorizing approved target emitted by the canonical P3 gate."""

    approved_target: ApprovedPortfolioTarget
    review: PortfolioRiskReview
    approval_evidence: PortfolioRiskApprovalEvidence


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _strategy_target(
    *,
    target_id: str,
    strategy_id: str,
    activation_id: str,
    generated_at: datetime,
    instrument_id: str,
    target_weight: float,
) -> StrategyTarget:
    return StrategyTarget(
        target_id=target_id,
        source_strategy_id=strategy_id,
        source_strategy_version="1.0.0",
        generated_at=generated_at,
        effective_at=generated_at + timedelta(minutes=1),
        expires_at=generated_at + timedelta(hours=2),
        positions=(TargetPosition(instrument_id, target_weight),),
        activation=StrategyTargetActivationRef(
            activation_id=activation_id,
            activation_hash=_hash(f"activation:{activation_id}"),
            approved_at=generated_at,
        ),
    )


def build_approved_portfolio_target_fixture(
    *,
    instrument_id: str = "RB2610",
) -> ApprovedPortfolioTargetFixture:
    """Create a two-source canonical composition and actual P3 approval."""

    source_generated_at = datetime(2026, 8, 23, 9, tzinfo=UTC)
    first = _strategy_target(
        target_id="generic-risk-source-a",
        strategy_id="generic.alpha",
        activation_id="generic-alpha-activation",
        generated_at=source_generated_at,
        instrument_id=instrument_id,
        target_weight=0.2,
    )
    second = _strategy_target(
        target_id="generic-risk-source-b",
        strategy_id="generic.beta",
        activation_id="generic-beta-activation",
        generated_at=source_generated_at,
        instrument_id=instrument_id,
        target_weight=0.1,
    )
    composition_generated_at = source_generated_at + timedelta(minutes=2)
    composition = CanonicalPortfolioComposer().compose(
        PortfolioCompositionRequest(
            target_id="generic-risk-portfolio",
            generated_at=composition_generated_at,
            effective_at=composition_generated_at + timedelta(minutes=1),
            expires_at=composition_generated_at + timedelta(hours=1),
            allocation_policy=AllocationPolicy(cash_reserve=0.1, target_volatility=0.1),
            allocation_inputs=(
                StrategyAllocationInput(first, 0.4, 0.1, 1.0, 0.4),
                StrategyAllocationInput(second, 0.3, 0.1, 1.0, 0.3),
            ),
        )
    )
    evaluated_at = composition.portfolio_target.effective_at + timedelta(minutes=1)
    observed_at = evaluated_at - timedelta(minutes=1)
    available_at = evaluated_at - timedelta(seconds=30)
    expires_at = evaluated_at + timedelta(minutes=5)
    account = PortfolioRiskAccountSnapshot(
        account_id="generic-fixture-account",
        equity=1_000_000.0,
        margin_capacity=500_000.0,
        observed_at=observed_at,
        available_at=available_at,
        expires_at=expires_at,
    )
    review_request = PortfolioRiskReviewRequest(
        composition=composition,
        account_snapshot=account,
        instrument_snapshots=(
            PortfolioRiskInstrumentSnapshot(
                instrument_id=instrument_id,
                commodity_id="RB",
                sector_id="ferrous",
                exchange_id="SHFE",
                correlation_cluster_id="china-steel",
                margin_fraction=0.1,
                observed_at=observed_at,
                available_at=available_at,
                expires_at=expires_at,
            ),
        ),
        risk_state=AccountScopedRiskStateEvidence(
            account_id=account.account_id,
            state_snapshot=RiskStateSnapshot.initial(occurred_at=source_generated_at),
            observed_at=observed_at,
            available_at=available_at,
            expires_at=expires_at,
        ),
        policy=build_portfolio_risk_policy(),
        evaluated_at=evaluated_at,
    )
    gate = PortfolioRiskApprovalGate()
    review = gate.review(review_request)
    approval_evidence = gate.evaluate(
        PortfolioRiskApprovalRequest(
            review_request=review_request,
            attestation=RiskApprovalAttestation(
                approval_id="generic-risk-approval",
                review_hash=review.review_hash,
                approver_id="risk-owner",
                approved_at=evaluated_at + timedelta(seconds=10),
                rationale="fixture-only generic risk approval",
            ),
        )
    )
    if approval_evidence.approved_target is None:
        raise AssertionError("fixture-only canonical risk approval must pass")
    return ApprovedPortfolioTargetFixture(
        approved_target=approval_evidence.approved_target,
        review=review,
        approval_evidence=approval_evidence,
    )


__all__ = ["ApprovedPortfolioTargetFixture", "build_approved_portfolio_target_fixture"]
