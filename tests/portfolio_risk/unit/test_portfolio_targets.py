from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.portfolio_risk.portfolio.targets import (
    PortfolioTarget,
    PortfolioTargetError,
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
)
from northstar_quant.portfolio_risk.portfolio import PortfolioRiskApprovalGate
from tests.helpers.canonical_portfolio_risk import build_approval_request
from tests.helpers.approved_portfolio_target import build_approved_portfolio_target_fixture


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _activation(*, activation_id: str, approved_at: datetime) -> StrategyTargetActivationRef:
    return StrategyTargetActivationRef(
        activation_id=activation_id,
        activation_hash=_hash(f"activation:{activation_id}"),
        approved_at=approved_at,
    )


def _strategy_target() -> StrategyTarget:
    now = datetime(2026, 8, 21, 9, tzinfo=UTC)
    return StrategyTarget(
        target_id="trend-target-1",
        source_strategy_id="futures.trend",
        source_strategy_version="1.0.0",
        generated_at=now,
        effective_at=now + timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        positions=(TargetPosition("SHFE.RB2610", 0.2),),
        activation=_activation(activation_id="manual-activation-1", approved_at=now),
    )


def test_targets_are_immutable_and_keep_strategy_then_portfolio_then_approval_lineage() -> None:
    strategy_target = _strategy_target()
    portfolio_target = PortfolioTarget(
        target_id="portfolio-target-1",
        generated_at=strategy_target.generated_at,
        effective_at=strategy_target.effective_at,
        expires_at=strategy_target.expires_at,
        source_strategy_target_hashes=(strategy_target.target_hash,),
        positions=(TargetPosition("SHFE.RB2610", 0.2),),
        composition_hash=_hash("portfolio-target-1-composition"),
    )
    fixture = build_approved_portfolio_target_fixture()
    approved = fixture.approved_target

    assert portfolio_target.source_strategy_target_hashes == (strategy_target.target_hash,)
    assert approved.portfolio_target.target_hash == fixture.review.portfolio_target.target_hash
    assert approved.risk_evidence_hash == fixture.review.review_hash
    assert approved.eligible_for_broker_order is False
    assert approved.as_mapping()["eligible_for_broker_order"] is False
    assert strategy_target.as_mapping()["activation"] == {
        "activation_id": "manual-activation-1",
        "activation_hash": _hash("activation:manual-activation-1"),
        "approved_at": strategy_target.generated_at.isoformat(),
    }


def test_target_contracts_fail_closed_for_invalid_windows_and_lineage() -> None:
    now = datetime(2026, 8, 21, 9, tzinfo=UTC)
    with pytest.raises(PortfolioTargetError, match="expires_at"):
        StrategyTarget(
            target_id="bad-target",
            source_strategy_id="futures.trend",
            source_strategy_version="1.0.0",
            generated_at=now,
            effective_at=now,
            expires_at=now,
            positions=(TargetPosition("SHFE.RB2610", 0.2),),
            activation=_activation(activation_id="invalid-window", approved_at=now),
        )
    with pytest.raises(PortfolioTargetError, match="activation approval must precede target effectiveness"):
        StrategyTarget(
            target_id="unapproved-target",
            source_strategy_id="futures.trend",
            source_strategy_version="1.0.0",
            generated_at=now,
            effective_at=now + timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
            positions=(TargetPosition("SHFE.RB2610", 0.2),),
            activation=_activation(
                activation_id="too-late-activation",
                approved_at=now + timedelta(minutes=1),
            ),
        )
    with pytest.raises(PortfolioTargetError, match="non-empty unique"):
        PortfolioTarget(
            target_id="portfolio-target-1",
            generated_at=now,
            effective_at=now,
            expires_at=now + timedelta(hours=1),
            source_strategy_target_hashes=(),
            positions=(TargetPosition("SHFE.RB2610", 0.2),),
            composition_hash=_hash("invalid-portfolio-composition"),
        )
    fixture = build_approved_portfolio_target_fixture()
    evidence = PortfolioRiskApprovalGate().evaluate(
        build_approval_request(
            fixture.approval_evidence.approval_request.review_request,
            approved_at=fixture.review.portfolio_target.expires_at,
        )
    )
    assert evidence.approved_target is None
    assert any(
        reason.startswith("ATTESTATION_AFTER_")
        for reason in evidence.rejection_reasons
    )
