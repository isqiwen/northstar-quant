"""Reusable fixture for P10-WP05 canonical portfolio-risk approval tests.

The values are deliberately fixture-only P3 inputs.  They are not market,
broker, account, execution, or production-authority data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from northstar_quant.portfolio_risk.limits import RiskLimitSet
from northstar_quant.portfolio_risk.portfolio import (
    AccountScopedRiskStateEvidence,
    PortfolioRiskAccountSnapshot,
    PortfolioRiskApprovalEvidence,
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
    PortfolioRiskInstrumentSnapshot,
    PortfolioRiskPolicy,
    PortfolioRiskReview,
    PortfolioRiskReviewRequest,
    PortfolioStressPolicy,
    RiskApprovalAttestation,
    StressScenarioLimit,
)
from northstar_quant.portfolio_risk.risk import RiskStateSnapshot, ScenarioKind, StressScenario
from tests.helpers.canonical_multi_strategy_portfolio import (
    CanonicalCompositionFixture,
    build_canonical_two_strategy_fixture,
)


@dataclass(frozen=True, slots=True)
class CanonicalPortfolioRiskFixture:
    """A passing two-strategy composition, review, and named P3 approval."""

    composition_fixture: CanonicalCompositionFixture
    account_snapshot: PortfolioRiskAccountSnapshot
    instrument_snapshots: tuple[PortfolioRiskInstrumentSnapshot, ...]
    risk_state: AccountScopedRiskStateEvidence
    policy: PortfolioRiskPolicy
    review_request: PortfolioRiskReviewRequest
    review: PortfolioRiskReview
    attestation: RiskApprovalAttestation
    approval_request: PortfolioRiskApprovalRequest
    approval_evidence: PortfolioRiskApprovalEvidence


def _review_times(composition: CanonicalCompositionFixture) -> tuple[datetime, datetime, datetime, datetime]:
    """Return observed, available, evaluated, and expiry times inside P10's window."""

    evaluated_at = composition.request.effective_at + timedelta(minutes=1)
    observed_at = evaluated_at - timedelta(minutes=1)
    available_at = evaluated_at - timedelta(seconds=30)
    expires_at = evaluated_at + timedelta(minutes=5)
    return observed_at, available_at, evaluated_at, expires_at


def build_portfolio_risk_policy() -> PortfolioRiskPolicy:
    """Return a passing, exact-nine-limit and exact-seven-scenario policy."""

    stress_policy = PortfolioStressPolicy(
        scenario_limits=tuple(
            StressScenarioLimit(
                scenario=StressScenario(
                    scenario_id=f"p10-{kind.value}",
                    kind=kind,
                    shock_fraction=0.1,
                ),
                max_loss_fraction=0.1,
                max_margin_utilization=0.2,
            )
            for kind in ScenarioKind
        )
    )
    return PortfolioRiskPolicy(
        policy_id="p10-portfolio-risk",
        policy_version="v1",
        authority_id="p10-fixture-authority",
        limits=RiskLimitSet(
            per_contract=0.5,
            per_commodity=0.5,
            per_sector=0.5,
            per_exchange=0.5,
            per_strategy=0.5,
            per_account=1.0,
            gross_leverage=0.8,
            net_leverage=0.8,
            margin_utilization=0.5,
        ),
        stress_policy=stress_policy,
        max_input_age_seconds=300,
    )


def build_approval_request(
    review_request: PortfolioRiskReviewRequest,
    *,
    approval_id: str = "p10-risk-approval",
    approver_id: str = "risk-owner",
    rationale: str = "fixture-only portfolio-risk review approved",
    review_hash: str | None = None,
    approved_at: datetime | None = None,
) -> PortfolioRiskApprovalRequest:
    """Build a named attestation against a freshly replayed review request."""

    review = PortfolioRiskApprovalGate().review(review_request)
    return PortfolioRiskApprovalRequest(
        review_request=review_request,
        attestation=RiskApprovalAttestation(
            approval_id=approval_id,
            review_hash=review.review_hash if review_hash is None else review_hash,
            approver_id=approver_id,
            approved_at=(
                review.evaluated_at + timedelta(seconds=10)
                if approved_at is None
                else approved_at
            ),
            rationale=rationale,
        ),
    )


def build_canonical_portfolio_risk_fixture() -> CanonicalPortfolioRiskFixture:
    """Build a full PASS review from the P10-WP04 two-strategy composition."""

    composition_fixture = build_canonical_two_strategy_fixture()
    observed_at, available_at, evaluated_at, expires_at = _review_times(composition_fixture)
    account_snapshot = PortfolioRiskAccountSnapshot(
        account_id="p10-fixture-account",
        equity=1_000_000.0,
        margin_capacity=500_000.0,
        observed_at=observed_at,
        available_at=available_at,
        expires_at=expires_at,
    )
    instrument_snapshots = (
        PortfolioRiskInstrumentSnapshot(
            instrument_id="SHFE.AU2610",
            commodity_id="AU",
            sector_id="precious",
            exchange_id="SHFE",
            correlation_cluster_id="china-metals",
            margin_fraction=0.08,
            observed_at=observed_at,
            available_at=available_at,
            expires_at=expires_at,
        ),
        PortfolioRiskInstrumentSnapshot(
            instrument_id="SHFE.CU2610",
            commodity_id="CU",
            sector_id="nonferrous",
            exchange_id="SHFE",
            correlation_cluster_id="china-metals",
            margin_fraction=0.10,
            observed_at=observed_at,
            available_at=available_at,
            expires_at=expires_at,
        ),
        PortfolioRiskInstrumentSnapshot(
            instrument_id="SHFE.RB2610",
            commodity_id="RB",
            sector_id="ferrous",
            exchange_id="SHFE",
            correlation_cluster_id="china-steel",
            margin_fraction=0.12,
            observed_at=observed_at,
            available_at=available_at,
            expires_at=expires_at,
        ),
    )
    risk_state = AccountScopedRiskStateEvidence(
        account_id=account_snapshot.account_id,
        state_snapshot=RiskStateSnapshot.initial(
            occurred_at=datetime(2026, 8, 23, 9, tzinfo=UTC)
        ),
        observed_at=observed_at,
        available_at=available_at,
        expires_at=expires_at,
    )
    policy = build_portfolio_risk_policy()
    review_request = PortfolioRiskReviewRequest(
        composition=composition_fixture.evidence,
        account_snapshot=account_snapshot,
        instrument_snapshots=instrument_snapshots,
        risk_state=risk_state,
        policy=policy,
        evaluated_at=evaluated_at,
    )
    gate = PortfolioRiskApprovalGate()
    review = gate.review(review_request)
    approval_request = build_approval_request(review_request)
    approval_evidence = gate.evaluate(approval_request)
    return CanonicalPortfolioRiskFixture(
        composition_fixture=composition_fixture,
        account_snapshot=account_snapshot,
        instrument_snapshots=instrument_snapshots,
        risk_state=risk_state,
        policy=policy,
        review_request=review_request,
        review=review,
        attestation=approval_request.attestation,
        approval_request=approval_request,
        approval_evidence=approval_evidence,
    )


__all__ = [
    "CanonicalPortfolioRiskFixture",
    "build_approval_request",
    "build_canonical_portfolio_risk_fixture",
    "build_portfolio_risk_policy",
]
