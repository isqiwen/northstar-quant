"""Closed typed tool facade for AI-assisted factor-mining campaigns.

This facade has exactly one capability: submit one already structured,
precommitted candidate batch to a trusted research-only discovery port.  It
cannot commit selection or release OOS evidence, and has no database,
filesystem, network, broker, portfolio, configuration, or runtime service
dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, TypeAlias

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.research.factor_mining.models import FactorCandidateGenerationReceipt
from northstar_quant.research.factor_mining.protocol import FactorMiningDiscoveryResult


__all__ = [
    "EvaluateFactorCandidateDiscoveryBatchRequest",
    "FactorMiningCampaignPort",
    "FactorMiningToolApi",
    "FactorMiningToolApiError",
    "FactorMiningToolDependencies",
    "FactorMiningToolName",
]


class FactorMiningToolApiError(ValueError):
    """Raised when the closed factor-mining tool contract is violated."""


class FactorMiningToolName(str, Enum):
    """The complete, non-tradable factor-mining capability allowlist."""

    EVALUATE_FACTOR_CANDIDATE_DISCOVERY_BATCH = "evaluate_factor_candidate_discovery_batch"


@dataclass(frozen=True, slots=True)
class EvaluateFactorCandidateDiscoveryBatchRequest:
    """Submit one hash-bound receipt for trusted non-OOS discovery evaluation."""

    generation: FactorCandidateGenerationReceipt
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.generation) is not FactorCandidateGenerationReceipt:
            raise FactorMiningToolApiError(
                "generation must be an exact FactorCandidateGenerationReceipt"
            )
        request_hash = canonical_json_sha256(
            {
                "format": "northstar.factor-mining-discovery-tool-request.v1",
                "generation_receipt_hash": self.generation.receipt_hash,
                "tool_name": (
                    FactorMiningToolName.EVALUATE_FACTOR_CANDIDATE_DISCOVERY_BATCH.value
                ),
            }
        )
        object.__setattr__(self, "request_hash", request_hash)


class FactorMiningCampaignPort(Protocol):
    """Trusted evaluator capability injected by an application composition root."""

    def evaluate_discovery_candidate_batch(
        self,
        *,
        request: EvaluateFactorCandidateDiscoveryBatchRequest,
    ) -> FactorMiningDiscoveryResult: ...


@dataclass(frozen=True, slots=True)
class FactorMiningToolDependencies:
    """Explicit dependency container for the sole trusted campaign capability."""

    campaign_port: FactorMiningCampaignPort


ToolRequest: TypeAlias = EvaluateFactorCandidateDiscoveryBatchRequest
ToolResponse: TypeAlias = FactorMiningDiscoveryResult


class FactorMiningToolApi:
    """A single-method typed facade; dynamic dispatch is intentionally absent."""

    __slots__ = ("_campaign_port",)

    def __init__(self, dependencies: FactorMiningToolDependencies) -> None:
        if type(dependencies) is not FactorMiningToolDependencies:
            raise FactorMiningToolApiError(
                "dependencies must be an exact FactorMiningToolDependencies"
            )
        self._campaign_port = dependencies.campaign_port

    def evaluate_discovery_candidate_batch(
        self,
        request: EvaluateFactorCandidateDiscoveryBatchRequest,
    ) -> FactorMiningDiscoveryResult:
        if type(request) is not EvaluateFactorCandidateDiscoveryBatchRequest:
            raise FactorMiningToolApiError(
                "request must be an exact EvaluateFactorCandidateDiscoveryBatchRequest"
            )
        result = self._campaign_port.evaluate_discovery_candidate_batch(request=request)
        if type(result) is not FactorMiningDiscoveryResult:
            raise FactorMiningToolApiError(
                "campaign port returned an unexpected discovery result type"
            )
        if (
            result.campaign_id != request.generation.campaign_id
            or result.campaign_hash != request.generation.campaign_hash
            or result.generation_receipt_hash != request.generation.receipt_hash
        ):
            raise FactorMiningToolApiError(
                "discovery result is not exactly bound to the submitted generation"
            )
        expected_candidates = {
            proposal.candidate_id: proposal.candidate_hash
            for proposal in request.generation.proposals
        }
        actual_candidates = {
            discovery.candidate_id: discovery.candidate_hash for discovery in result.results
        }
        if actual_candidates != expected_candidates:
            raise FactorMiningToolApiError(
                "discovery result must contain one exact result for every submitted candidate"
            )
        return result

    def invoke(
        self,
        tool_name: FactorMiningToolName,
        request: ToolRequest,
    ) -> ToolResponse:
        if type(tool_name) is not FactorMiningToolName:
            raise FactorMiningToolApiError("tool_name must be FactorMiningToolName")
        if tool_name is FactorMiningToolName.EVALUATE_FACTOR_CANDIDATE_DISCOVERY_BATCH:
            return self.evaluate_discovery_candidate_batch(request)
        raise FactorMiningToolApiError("tool_name is outside the closed allowlist")
