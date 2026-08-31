"""Validation for bounded, declarative factor-mining candidates."""

from __future__ import annotations

from northstar_quant.research.factors.models import FactorDefinition, FactorResearchError, FactorRole
from northstar_quant.research.factor_mining.models import (
    CandidateValidationStatus,
    FactorCandidateProposal,
    FactorCandidateValidation,
    FactorMiningCampaignSpec,
    FactorMiningError,
)


__all__ = ["validate_factor_candidate"]


def validate_factor_candidate(
    *,
    campaign: FactorMiningCampaignSpec,
    candidate: FactorCandidateProposal,
) -> FactorCandidateValidation:
    """Validate one AI proposal without executing it or reading any market data.

    The policy owns the feature primitive, parameter grid, direction, risk
    budget, costs, and OOS folds.  The candidate can only select one permitted
    primitive and one finite point in its parameter grid.
    """

    if type(campaign) is not FactorMiningCampaignSpec:
        raise FactorMiningError("campaign must be an exact FactorMiningCampaignSpec")
    if type(candidate) is not FactorCandidateProposal:
        raise FactorMiningError("candidate must be an exact FactorCandidateProposal")
    if candidate.campaign_id != campaign.campaign_id:
        return _rejected(campaign, candidate, "CAMPAIGN_ID_MISMATCH")

    primitive = campaign.primitive(candidate.primitive_id)
    if primitive is None:
        return _rejected(campaign, candidate, "UNKNOWN_PRIMITIVE")
    if candidate.direction not in primitive.allowed_directions:
        return _rejected(campaign, candidate, "DIRECTION_NOT_ALLOWED")

    parameters = candidate.parameters
    if tuple(sorted(parameters)) != primitive.parameter_names:
        return _rejected(campaign, candidate, "PARAMETER_SCHEMA_NOT_ALLOWED")
    for domain in primitive.parameter_domains:
        if not domain.allows(parameters[domain.name]):
            return _rejected(campaign, candidate, "PARAMETER_VALUE_NOT_ALLOWED")

    try:
        definition = FactorDefinition.create(
            factor_id=f"alpha_{candidate.candidate_id}",
            feature_id=primitive.feature_id,
            role=FactorRole.ALPHA,
            direction=candidate.direction,
            risk_budget=1.0,
            parameters=parameters,
        )
    except FactorResearchError:
        return _rejected(campaign, candidate, "FACTOR_DEFINITION_REJECTED")

    return FactorCandidateValidation(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate=candidate,
        status=CandidateValidationStatus.VALIDATED_FOR_RESEARCH,
        reason_code="VALIDATED_FOR_RESEARCH",
        factor_definition=definition,
    )


def _rejected(
    campaign: FactorMiningCampaignSpec,
    candidate: FactorCandidateProposal,
    reason_code: str,
) -> FactorCandidateValidation:
    return FactorCandidateValidation(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate=candidate,
        status=CandidateValidationStatus.REJECTED,
        reason_code=reason_code,
        factor_definition=None,
    )
