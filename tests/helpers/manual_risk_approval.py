"""Test-only verifier for the P10 manual portfolio-risk approval boundary."""

from __future__ import annotations

from dataclasses import dataclass

from northstar_quant.application.portfolio_risk_manual_approval import (
    ManualRiskApprovalBinding,
    _PortfolioRiskApprovalIssuer,
    _VerifiedManualRiskApproval,
    _create_portfolio_risk_approval_issuer_for_test,
    _verified_manual_risk_approval_from_trusted_verifier,
)
from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256


@dataclass(frozen=True, slots=True)
class FakeManualRiskApprovalVerifier:
    """Deterministic in-memory verifier; never represents a production proof."""

    verifier_id: str = "ctp-sim-manual-risk-verifier-v1"
    approver_id: str = "risk-owner"
    rationale: str = "test-only manual portfolio-risk approval"

    def verify(
        self,
        *,
        binding: ManualRiskApprovalBinding,
    ) -> _VerifiedManualRiskApproval:
        return _verified_manual_risk_approval_from_trusted_verifier(
            binding=binding,
            approver_id=self.approver_id,
            verifier_id=self.verifier_id,
            verifier_receipt_hash=canonical_json_sha256(
                {
                    "format": "northstar.test-only-manual-risk-verifier.v1",
                    "binding_hash": binding.binding_hash,
                    "approver_id": self.approver_id,
                }
            ),
            approved_at=binding.review_evaluated_at,
            verified_at=binding.review_evaluated_at,
            rationale=self.rationale,
        )


def create_test_portfolio_risk_approval_issuer(
    verifier: FakeManualRiskApprovalVerifier | None = None,
) -> _PortfolioRiskApprovalIssuer:
    """Return a fake-backed issuer only from test composition code."""

    return _create_portfolio_risk_approval_issuer_for_test(
        FakeManualRiskApprovalVerifier() if verifier is None else verifier
    )


__all__ = [
    "FakeManualRiskApprovalVerifier",
    "create_test_portfolio_risk_approval_issuer",
]
