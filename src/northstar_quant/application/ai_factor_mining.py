"""Least-privilege orchestration for an AI-proposed factor-mining batch.

The injected generator receives only a sealed, metadata-only campaign request.
It cannot receive a DecisionReplayPlan object, market data, a FeatureRegistry,
an ArtifactStore, a portfolio target, or a trading capability.  Evaluation is
available solely through FactorMiningToolApi's closed typed method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from typing import Literal, Protocol

from northstar_quant.application.factor_mining_tools import (
    EvaluateFactorCandidateDiscoveryBatchRequest,
    FactorMiningToolApi,
)
from northstar_quant.research.factor_mining.models import (
    FactorCandidateGenerationReceipt,
    FactorCandidateGenerationRequest,
    FactorMiningCampaignSpec,
)
from northstar_quant.research.factor_mining.protocol import FactorMiningDiscoveryResult


__all__ = [
    "AIFactorMiningAgent",
    "AIFactorMiningAgentError",
    "AIFactorMiningAgentRequest",
    "AIFactorMiningAgentResult",
    "FactorCandidateGenerator",
    "ai_factor_mining_request_hash",
]


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class AIFactorMiningAgentError(ValueError):
    """Raised when an AI campaign is incomplete, unsafe, or automatically retried."""


def _run_id(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None or value == "latest":
        raise AIFactorMiningAgentError("run_id must be a lower-case stable identifier")
    return value


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class FactorCandidateGenerator(Protocol):
    """A provider adapter which only receives a redacted campaign request."""

    def generate(
        self,
        request: FactorCandidateGenerationRequest,
    ) -> FactorCandidateGenerationReceipt: ...


@dataclass(frozen=True, slots=True)
class AIFactorMiningAgentRequest:
    """One precommitted one-shot AI factor-mining request."""

    run_id: str
    campaign: FactorMiningCampaignSpec

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _run_id(self.run_id))
        if type(self.campaign) is not FactorMiningCampaignSpec:
            raise AIFactorMiningAgentError("campaign must be an exact FactorMiningCampaignSpec")


def ai_factor_mining_request_hash(request: AIFactorMiningAgentRequest) -> str:
    """Return the secret-free immutable commitment for an agent request."""

    if type(request) is not AIFactorMiningAgentRequest:
        raise AIFactorMiningAgentError("request must be an exact AIFactorMiningAgentRequest")
    return _fingerprint(
        {
            "campaign_hash": request.campaign.campaign_hash,
            "format": "northstar.ai-factor-mining-agent-request.v1",
            "run_id": request.run_id,
        }
    )


@dataclass(frozen=True, slots=True)
class AIFactorMiningAgentResult:
    """The non-OOS discovery result; it cannot activate a strategy or release OOS."""

    run_id: str
    request_hash: str
    generation: FactorCandidateGenerationReceipt
    discovery_result: FactorMiningDiscoveryResult
    lifecycle: Literal["RESEARCH_ONLY"] = field(default="RESEARCH_ONLY", init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def __post_init__(self) -> None:
        run_id = _run_id(self.run_id)
        if not isinstance(self.request_hash, str) or re.fullmatch(r"[0-9a-f]{64}", self.request_hash) is None:
            raise AIFactorMiningAgentError("request_hash must be a lower-case SHA-256")
        if type(self.generation) is not FactorCandidateGenerationReceipt:
            raise AIFactorMiningAgentError(
                "generation must be an exact FactorCandidateGenerationReceipt"
            )
        if type(self.discovery_result) is not FactorMiningDiscoveryResult:
            raise AIFactorMiningAgentError(
                "discovery_result must be an exact FactorMiningDiscoveryResult"
            )
        if (
            self.discovery_result.campaign_id != self.generation.campaign_id
            or self.discovery_result.campaign_hash != self.generation.campaign_hash
            or self.discovery_result.generation_receipt_hash != self.generation.receipt_hash
        ):
            raise AIFactorMiningAgentError(
                "discovery result must be exactly bound to the generation receipt"
            )
        if not (
            self.discovery_result.research_only
            and not self.discovery_result.candidate_admission_eligible
            and not self.discovery_result.simnow_handoff_allowed
        ):
            raise AIFactorMiningAgentError("discovery result must remain research-only")
        object.__setattr__(self, "run_id", run_id)


class AIFactorMiningAgent:
    """Run one generator batch through the sole non-OOS discovery capability."""

    __slots__ = ("_generator", "_seen_request_hashes", "_seen_run_ids", "_tool_api")

    def __init__(
        self,
        *,
        generator: FactorCandidateGenerator,
        tool_api: FactorMiningToolApi,
    ) -> None:
        if type(tool_api) is not FactorMiningToolApi:
            raise AIFactorMiningAgentError("tool_api must be an exact FactorMiningToolApi")
        self._generator = generator
        self._tool_api = tool_api
        self._seen_request_hashes: set[str] = set()
        self._seen_run_ids: set[str] = set()

    def run(self, request: AIFactorMiningAgentRequest) -> AIFactorMiningAgentResult:
        """Generate once, validate/evaluate once, and never automatically retry.

        The generator is called before any OOS result exists and receives no
        result callback.  This boundary cannot create a selection commitment
        or release OOS evidence.  A subsequent adaptive proposal requires a
        new sealed campaign and a new human-approved scheduling decision.
        """

        if type(request) is not AIFactorMiningAgentRequest:
            raise AIFactorMiningAgentError("request must be an exact AIFactorMiningAgentRequest")
        request_hash = ai_factor_mining_request_hash(request)
        if request.run_id in self._seen_run_ids or request_hash in self._seen_request_hashes:
            raise AIFactorMiningAgentError(
                "an AI factor-mining request cannot be automatically replayed or retried"
            )
        # Reserve before calling the external adapter.  A failure cannot prove
        # that a provider-side action did not happen, so retries are forbidden.
        self._seen_run_ids.add(request.run_id)
        self._seen_request_hashes.add(request_hash)
        generation = self._generator.generate(
            FactorCandidateGenerationRequest(campaign=request.campaign)
        )
        if type(generation) is not FactorCandidateGenerationReceipt:
            raise AIFactorMiningAgentError(
                "generator must return an exact FactorCandidateGenerationReceipt"
            )
        generation.require_campaign(request.campaign)
        discovery_result = self._tool_api.evaluate_discovery_candidate_batch(
            EvaluateFactorCandidateDiscoveryBatchRequest(generation=generation)
        )
        return AIFactorMiningAgentResult(
            run_id=request.run_id,
            request_hash=request_hash,
            generation=generation,
            discovery_result=discovery_result,
        )
