"""Portfolio target and allocation contracts."""

from typing import TYPE_CHECKING, Any

from northstar_quant.portfolio_risk.portfolio.targets import (
    PortfolioTarget,
    PortfolioTargetError,
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
)

if TYPE_CHECKING:
    from northstar_quant.portfolio_risk.portfolio.approval import (
        AccountScopedRiskStateEvidence,
        ApprovedPortfolioTarget,
        PortfolioRiskAccountSnapshot,
        PortfolioRiskApprovalError,
        PortfolioRiskApprovalEvidence,
        PortfolioRiskApprovalGate,
        PortfolioRiskApprovalRequest,
        PortfolioRiskInstrumentSnapshot,
        PortfolioRiskPolicy,
        PortfolioRiskPosition,
        PortfolioRiskReview,
        PortfolioRiskReviewRequest,
        PortfolioRiskReviewStatus,
        PortfolioStressCheck,
        PortfolioStressPolicy,
        RiskApprovalAttestation,
        StressScenarioLimit,
    )
    from northstar_quant.portfolio_risk.portfolio.composition import (
        CanonicalPortfolioComposer,
        PortfolioCompositionError,
        PortfolioCompositionEvidence,
        PortfolioCompositionRequest,
        StrategyTargetContribution,
    )


_COMPOSITION_EXPORTS = frozenset(
    {
        "CanonicalPortfolioComposer",
        "PortfolioCompositionError",
        "PortfolioCompositionEvidence",
        "PortfolioCompositionRequest",
        "StrategyTargetContribution",
    }
)

_APPROVAL_EXPORTS = frozenset(
    {
        "AccountScopedRiskStateEvidence",
        "ApprovedPortfolioTarget",
        "PortfolioRiskAccountSnapshot",
        "PortfolioRiskApprovalError",
        "PortfolioRiskApprovalEvidence",
        "PortfolioRiskApprovalGate",
        "PortfolioRiskApprovalRequest",
        "PortfolioRiskInstrumentSnapshot",
        "PortfolioRiskPolicy",
        "PortfolioRiskPosition",
        "PortfolioRiskReview",
        "PortfolioRiskReviewRequest",
        "PortfolioRiskReviewStatus",
        "PortfolioStressCheck",
        "PortfolioStressPolicy",
        "RiskApprovalAttestation",
        "StressScenarioLimit",
    }
)


def __getattr__(name: str) -> Any:
    """Avoid a target/allocation package-import cycle while keeping one P3 API."""

    if name not in _COMPOSITION_EXPORTS and name not in _APPROVAL_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in _COMPOSITION_EXPORTS:
        from northstar_quant.portfolio_risk.portfolio import composition

        return getattr(composition, name)
    from northstar_quant.portfolio_risk.portfolio import approval

    return getattr(approval, name)

__all__ = [
    "ApprovedPortfolioTarget",
    "AccountScopedRiskStateEvidence",
    "CanonicalPortfolioComposer",
    "PortfolioCompositionError",
    "PortfolioCompositionEvidence",
    "PortfolioCompositionRequest",
    "PortfolioRiskAccountSnapshot",
    "PortfolioRiskApprovalError",
    "PortfolioRiskApprovalEvidence",
    "PortfolioRiskApprovalGate",
    "PortfolioRiskApprovalRequest",
    "PortfolioRiskInstrumentSnapshot",
    "PortfolioRiskPolicy",
    "PortfolioRiskPosition",
    "PortfolioRiskReview",
    "PortfolioRiskReviewRequest",
    "PortfolioRiskReviewStatus",
    "PortfolioStressCheck",
    "PortfolioStressPolicy",
    "PortfolioTarget",
    "PortfolioTargetError",
    "StrategyTarget",
    "StrategyTargetActivationRef",
    "StrategyTargetContribution",
    "RiskApprovalAttestation",
    "StressScenarioLimit",
    "TargetPosition",
]
