"""Research-only contracts for bounded AI-assisted factor mining.

The public factor-mining boundary intentionally distinguishes a generated
candidate proposal from development-only discovery evidence, an immutable
selection commitment, and an explicit OOS release. Discovery contracts do not
expose OOS or full-run artifacts.
"""

from northstar_quant.research.factor_mining.models import (
    CandidateValidationStatus,
    FactorCandidateGenerationReceipt,
    FactorCandidateGenerationRequest,
    FactorCandidateProposal,
    FactorCandidateValidation,
    FactorMiningCampaignSpec,
    FactorMiningCostScenario,
    FactorMiningError,
    FactorMiningMultipleTestingControl,
    FactorMiningSelectionPolicy,
    FactorMiningStageBoundaryMode,
    FactorMiningRunnerResourceBudget,
    FactorParameterDomain,
    FactorPipelineTemplate,
    FactorPrimitive,
    FactorSearchBudget,
)
from northstar_quant.research.factor_mining.protocol import (
    CandidateDiscoveryDisposition,
    FactorCandidateDiscoveryResult,
    FactorDiscoveryStageCostResult,
    FactorMiningDiscoveryResult,
    FactorMiningOOSRelease,
    FactorMiningOOSReleaseResult,
    FactorMiningSelectionCommitment,
    FactorMiningSelectionDisposition,
    FactorMiningSelectionRecord,
    FactorMiningStageEvidence,
    select_discovery_candidates,
)
from northstar_quant.research.factor_mining.run_bundle import (
    GovernedResearchArtifactKind,
    GovernedResearchArtifactReference,
    LocalFactorMiningCampaignDeclaration,
    LocalFactorMiningRunBundle,
    LocalFactorMiningRunBundleError,
    LocalFactorMiningRunConfig,
    LocalFactorMiningRunManifest,
)
from northstar_quant.research.factor_mining.validator import validate_factor_candidate

__all__ = [
    "CandidateDiscoveryDisposition",
    "CandidateValidationStatus",
    "FactorCandidateDiscoveryResult",
    "FactorCandidateGenerationReceipt",
    "FactorCandidateGenerationRequest",
    "FactorCandidateProposal",
    "FactorCandidateValidation",
    "FactorMiningCampaignSpec",
    "FactorMiningCostScenario",
    "FactorDiscoveryStageCostResult",
    "FactorMiningDiscoveryResult",
    "FactorMiningError",
    "FactorMiningMultipleTestingControl",
    "FactorMiningOOSRelease",
    "FactorMiningOOSReleaseResult",
    "FactorMiningSelectionCommitment",
    "FactorMiningSelectionDisposition",
    "FactorMiningSelectionPolicy",
    "FactorMiningSelectionRecord",
    "FactorMiningStageBoundaryMode",
    "FactorMiningRunnerResourceBudget",
    "FactorMiningStageEvidence",
    "FactorParameterDomain",
    "FactorPipelineTemplate",
    "FactorPrimitive",
    "FactorSearchBudget",
    "GovernedResearchArtifactKind",
    "GovernedResearchArtifactReference",
    "LocalFactorMiningCampaignDeclaration",
    "LocalFactorMiningRunBundle",
    "LocalFactorMiningRunBundleError",
    "LocalFactorMiningRunConfig",
    "LocalFactorMiningRunManifest",
    "select_discovery_candidates",
    "validate_factor_candidate",
]
