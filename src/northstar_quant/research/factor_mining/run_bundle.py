"""Immutable, declarative input and result contracts for local factor-mining runs.

The local command surface never accepts a DataFrame, a ``latest`` selector, a
notebook state, or a filesystem input path.  It accepts one already-published
bundle artifact whose payload reconstructs only the narrowly whitelisted
research declarations in this module.  The application composition root then
re-validates its DatasetVersion lineage before it can run anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
import json
import math
import re
from typing import Any, TypeAlias, cast

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.data.market.pit import MarketDataKind, MarketDataPITSpec
from northstar_quant.research.factor_mining.models import (
    FactorCandidateGenerationReceipt,
    FactorCandidateProposal,
    FactorMiningCampaignSpec,
    FactorMiningCostScenario,
    FactorMiningMultipleTestingControl,
    FactorMiningSelectionPolicy,
    FactorMiningStageBoundaryMode,
    FactorMiningRunnerResourceBudget,
    FactorParameterDomain,
    FactorPipelineTemplate,
    FactorPrimitive,
    FactorSearchBudget,
)
from northstar_quant.research.factors.models import (
    FactorAnalysisPeriod,
    FactorAnalysisResult,
    FactorDefinition,
    FactorPipelineConfig,
    FactorResearchExperiment,
    FactorResearchRunManifest,
    FactorRobustnessCostScenario,
    FactorRobustnessCostScenarioResult,
    FactorRobustnessFactorSummary,
    FactorRobustnessParameterVariant,
    FactorRobustnessParameterVariantResult,
    FactorRobustnessPlan,
    FactorRobustnessResult,
    FactorRobustnessScenarioResult,
    FactorRobustnessSubperiod,
    FactorRole,
    FactorStabilityThresholds,
    FactorWalkForwardResult,
)
from northstar_quant.research.validation.framework import (
    ValidationPeriod,
    ValidationSplit,
    WalkForwardFold,
)
from northstar_quant.research.validation.lookahead import (
    DecisionReplayCheckpoint,
    DecisionReplayPlan,
)


__all__ = [
    "GovernedResearchArtifactKind",
    "GovernedResearchArtifactReference",
    "FactorResearchOOSRobustnessProof",
    "LocalFactorMiningCampaignDeclaration",
    "LocalFactorMiningRunBundle",
    "LocalFactorMiningRunConfig",
    "LocalFactorMiningRunManifest",
    "LocalFactorMiningRunBundleError",
    "decode_factor_research_oos_robustness_proof",
    "encode_factor_research_oos_robustness_proof",
    "project_factor_research_oos_robustness_proof",
]


_BUNDLE_FORMAT = "northstar.local-factor-mining-run-bundle.v1"
_CAMPAIGN_DECLARATION_FORMAT = "northstar.local-factor-mining-campaign-declaration.v1"
_MANIFEST_FORMAT = "northstar.local-factor-mining-run-manifest.v1"
_OOS_ROBUSTNESS_PROOF_FORMAT = "northstar.factor-research-oos-robustness-proof.v1"
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\\\/]")


class LocalFactorMiningRunBundleError(ValueError):
    """A local bundle is incomplete, ambiguous, or not safe to replay."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise LocalFactorMiningRunBundleError(
            f"{field_name} must be a lower-case stable identifier"
        )
    if value == "latest":
        raise LocalFactorMiningRunBundleError(f"{field_name} cannot use latest")
    return value


def _safe_token(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value.strip()) is None:
        raise LocalFactorMiningRunBundleError(f"{field_name} must be a bounded opaque token")
    normalized = value.strip()
    if normalized.casefold() == "latest" or _ABSOLUTE_PATH_RE.match(normalized):
        raise LocalFactorMiningRunBundleError(f"{field_name} cannot use an ambiguous selector")
    return normalized


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise LocalFactorMiningRunBundleError(str(exc)) from exc


def _hashes(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise LocalFactorMiningRunBundleError(f"{field_name} must be a non-empty hash tuple")
    hashes = tuple(sorted(_hash(item, field_name) for item in value))
    if not hashes or len(hashes) != len(set(hashes)):
        raise LocalFactorMiningRunBundleError(
            f"{field_name} must contain one or more unique hashes"
        )
    return hashes


def _optional_hash_or_empty(value: object, field_name: str) -> str | None:
    """Accept only the explicit empty-string omission used by typed constructors."""

    if type(value) is not str:
        raise LocalFactorMiningRunBundleError(
            f"{field_name} must be a SHA-256 hash or an explicit empty string"
        )
    return None if value == "" else _hash(value, field_name)


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalFactorMiningRunBundleError("bundle payload must be canonical JSON") from exc


@dataclass(frozen=True, slots=True)
class LocalFactorMiningRunConfig:
    """Fixed local-output policy; it intentionally has no live/trading fields."""

    config_id: str
    code_revision: str
    code_revision_hash: str
    output_schema_version: str
    output_transform_version: str
    retention_days: int
    automatic_cleanup: bool
    config_hash: str = ""

    def __post_init__(self) -> None:
        config_id = _identifier(self.config_id, "run_config.config_id")
        code_revision = _safe_token(self.code_revision, "run_config.code_revision")
        code_revision_hash = _hash(
            self.code_revision_hash,
            "run_config.code_revision_hash",
        )
        schema_version = _safe_token(
            self.output_schema_version,
            "run_config.output_schema_version",
        )
        transform_version = _safe_token(
            self.output_transform_version,
            "run_config.output_transform_version",
        )
        if isinstance(self.retention_days, bool) or not isinstance(self.retention_days, int):
            raise LocalFactorMiningRunBundleError("run_config.retention_days must be an integer")
        if not 1 <= self.retention_days <= 36_500:
            raise LocalFactorMiningRunBundleError(
                "run_config.retention_days must be between 1 and 36500"
            )
        if self.automatic_cleanup is not False:
            raise LocalFactorMiningRunBundleError(
                "local research evidence must not enable automatic cleanup"
            )
        config_hash = canonical_json_sha256(
            {
                "automatic_cleanup": False,
                "code_revision": code_revision,
                "code_revision_hash": code_revision_hash,
                "config_id": config_id,
                "format": "northstar.local-factor-mining-run-config.v1",
                "output_schema_version": schema_version,
                "output_transform_version": transform_version,
                "retention_days": self.retention_days,
            }
        )
        supplied_config_hash = _optional_hash_or_empty(
            self.config_hash,
            "run_config.config_hash",
        )
        if supplied_config_hash is not None and supplied_config_hash != config_hash:
            raise LocalFactorMiningRunBundleError("run_config.config_hash does not match fixed fields")
        object.__setattr__(self, "config_id", config_id)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "code_revision_hash", code_revision_hash)
        object.__setattr__(self, "output_schema_version", schema_version)
        object.__setattr__(self, "output_transform_version", transform_version)
        object.__setattr__(self, "config_hash", config_hash)

    def retention_mapping(self) -> dict[str, object]:
        return {
            "automatic_cleanup": False,
            "policy_id": self.config_id,
            "retention_days": self.retention_days,
        }


@dataclass(frozen=True, slots=True)
class LocalFactorMiningCampaignDeclaration:
    """Sealed pre-generation input for a bounded local mining campaign.

    Unlike :class:`LocalFactorMiningRunBundle`, this declaration deliberately
    has no provider receipt or candidate proposal.  A durable application
    runner reserves the request first, then records one redacted generation
    receipt and creates the corresponding run bundle.  This keeps the
    campaign/request/receipt audit ordering unambiguous after a crash.
    """

    dataset_version_hashes: tuple[str, ...]
    plan: DecisionReplayPlan
    campaign: FactorMiningCampaignSpec
    config: LocalFactorMiningRunConfig
    runner_budget: FactorMiningRunnerResourceBudget
    declaration_hash: str = ""

    def __post_init__(self) -> None:
        dataset_hashes = _hashes(
            self.dataset_version_hashes,
            "campaign_declaration.dataset_version_hashes",
        )
        if type(self.plan) is not DecisionReplayPlan:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration plan must be an exact DecisionReplayPlan"
            )
        if type(self.campaign) is not FactorMiningCampaignSpec:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration campaign must be an exact FactorMiningCampaignSpec"
            )
        if type(self.config) is not LocalFactorMiningRunConfig:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration config must be an exact LocalFactorMiningRunConfig"
            )
        if type(self.runner_budget) is not FactorMiningRunnerResourceBudget:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration runner_budget must be an exact FactorMiningRunnerResourceBudget"
            )
        plan_datasets = tuple(
            sorted({checkpoint.dataset_version_hash for checkpoint in self.plan.checkpoints})
        )
        if dataset_hashes != plan_datasets:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration DatasetVersion hashes must exactly match every replay checkpoint"
            )
        if self.campaign.decision_replay_plan_hash != self.plan.schedule_hash:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration campaign does not bind the replay plan"
            )
        if self.campaign.dataset_version_hashes != dataset_hashes:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration campaign datasets do not exactly match the replay plan"
            )
        if self.config.code_revision != self.campaign.template.code_revision:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration code_revision must match the sealed campaign template"
            )
        # The generator receives the sealed campaign request directly.  A
        # smaller runner cap would therefore only reject *after* a provider
        # had already been allowed to generate above that cap.  Until the
        # generation request itself carries a separately sealed effective
        # cap, both limits must be identical at the declaration boundary.
        if self.runner_budget.max_candidates != self.campaign.budget.max_candidates:
            raise LocalFactorMiningRunBundleError(
                "runner candidate budget must exactly match the sealed campaign candidate budget"
            )
        expected_hash = canonical_json_sha256(
            {
                "campaign_hash": self.campaign.campaign_hash,
                "config_hash": self.config.config_hash,
                "dataset_version_hashes": list(dataset_hashes),
                "decision_replay_plan_hash": self.plan.schedule_hash,
                "format": _CAMPAIGN_DECLARATION_FORMAT,
                "runner_budget_hash": self.runner_budget.budget_hash,
            }
        )
        supplied_hash = _optional_hash_or_empty(
            self.declaration_hash,
            "campaign_declaration.declaration_hash",
        )
        if supplied_hash is not None and supplied_hash != expected_hash:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration hash does not bind the exact declaration"
            )
        object.__setattr__(self, "dataset_version_hashes", dataset_hashes)
        object.__setattr__(self, "declaration_hash", expected_hash)

    def to_payload(self) -> dict[str, object]:
        return {
            "campaign_declaration": _encode_wire_value(self),
            "declaration_hash": self.declaration_hash,
            "format": _CAMPAIGN_DECLARATION_FORMAT,
        }

    def to_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_payload())

    @classmethod
    def from_bytes(cls, payload: bytes) -> "LocalFactorMiningCampaignDeclaration":
        if not isinstance(payload, bytes):
            raise LocalFactorMiningRunBundleError(
                "campaign declaration artifact payload must be bytes"
            )
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration artifact payload must be JSON"
            ) from exc
        if _canonical_json_bytes(decoded) != payload:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration artifact payload must be canonical JSON"
            )
        if not isinstance(decoded, dict) or set(decoded) != {
            "campaign_declaration",
            "declaration_hash",
            "format",
        }:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration artifact payload has an unsupported shape"
            )
        if decoded["format"] != _CAMPAIGN_DECLARATION_FORMAT:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration artifact format is unsupported"
            )
        _reject_unsafe_wire_text(decoded)
        value = _decode_wire_value(decoded["campaign_declaration"])
        if type(value) is not cls:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration artifact does not contain a typed declaration"
            )
        supplied_hash = _hash(
            decoded["declaration_hash"],
            "campaign_declaration_artifact.declaration_hash",
        )
        if value.declaration_hash != supplied_hash:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration artifact hash does not match its declaration"
            )
        if value.to_bytes() != payload:
            raise LocalFactorMiningRunBundleError(
                "campaign declaration artifact is not the canonical typed declaration"
            )
        return value


@dataclass(frozen=True, slots=True)
class LocalFactorMiningRunBundle:
    """The only local factor-mining input shape accepted by the run service."""

    dataset_version_hashes: tuple[str, ...]
    plan: DecisionReplayPlan
    campaign: FactorMiningCampaignSpec
    generation: FactorCandidateGenerationReceipt
    config: LocalFactorMiningRunConfig
    bundle_hash: str = ""

    @classmethod
    def from_campaign_declaration(
        cls,
        *,
        declaration: LocalFactorMiningCampaignDeclaration,
        generation: FactorCandidateGenerationReceipt,
    ) -> "LocalFactorMiningRunBundle":
        """Bind one post-reservation generation receipt to a sealed campaign.

        The declaration remains immutable and receipt-free.  This factory is
        the sole narrow conversion into the existing full local-research bundle.
        """

        if type(declaration) is not LocalFactorMiningCampaignDeclaration:
            raise LocalFactorMiningRunBundleError(
                "declaration must be an exact LocalFactorMiningCampaignDeclaration"
            )
        if type(generation) is not FactorCandidateGenerationReceipt:
            raise LocalFactorMiningRunBundleError(
                "generation must be an exact FactorCandidateGenerationReceipt"
            )
        return cls(
            dataset_version_hashes=declaration.dataset_version_hashes,
            plan=declaration.plan,
            campaign=declaration.campaign,
            generation=generation,
            config=declaration.config,
        )

    def __post_init__(self) -> None:
        dataset_hashes = _hashes(self.dataset_version_hashes, "bundle.dataset_version_hashes")
        if type(self.plan) is not DecisionReplayPlan:
            raise LocalFactorMiningRunBundleError("bundle.plan must be an exact DecisionReplayPlan")
        if type(self.campaign) is not FactorMiningCampaignSpec:
            raise LocalFactorMiningRunBundleError(
                "bundle.campaign must be an exact FactorMiningCampaignSpec"
            )
        if type(self.generation) is not FactorCandidateGenerationReceipt:
            raise LocalFactorMiningRunBundleError(
                "bundle.generation must be an exact FactorCandidateGenerationReceipt"
            )
        if type(self.config) is not LocalFactorMiningRunConfig:
            raise LocalFactorMiningRunBundleError(
                "bundle.config must be an exact LocalFactorMiningRunConfig"
            )
        plan_datasets = tuple(
            sorted({checkpoint.dataset_version_hash for checkpoint in self.plan.checkpoints})
        )
        if dataset_hashes != plan_datasets:
            raise LocalFactorMiningRunBundleError(
                "bundle DatasetVersion hashes must exactly match every replay checkpoint"
            )
        if self.campaign.decision_replay_plan_hash != self.plan.schedule_hash:
            raise LocalFactorMiningRunBundleError("bundle campaign does not bind the replay plan")
        if self.campaign.dataset_version_hashes != dataset_hashes:
            raise LocalFactorMiningRunBundleError(
                "bundle campaign DatasetVersion hashes do not exactly match the replay plan"
            )
        if self.config.code_revision != self.campaign.template.code_revision:
            raise LocalFactorMiningRunBundleError(
                "fixed run config code_revision must match the sealed campaign template"
            )
        try:
            self.generation.require_campaign(self.campaign)
        except ValueError as exc:
            raise LocalFactorMiningRunBundleError(
                "bundle generation receipt does not exactly bind the sealed campaign"
            ) from exc
        expected_hash = canonical_json_sha256(
            {
                "campaign_hash": self.campaign.campaign_hash,
                "config_hash": self.config.config_hash,
                "dataset_version_hashes": list(dataset_hashes),
                "decision_replay_plan_hash": self.plan.schedule_hash,
                "format": _BUNDLE_FORMAT,
                "generation_receipt_hash": self.generation.receipt_hash,
            }
        )
        supplied_bundle_hash = _optional_hash_or_empty(
            self.bundle_hash,
            "bundle.bundle_hash",
        )
        if supplied_bundle_hash is not None and supplied_bundle_hash != expected_hash:
            raise LocalFactorMiningRunBundleError("bundle_hash does not bind the exact declaration")
        object.__setattr__(self, "dataset_version_hashes", dataset_hashes)
        object.__setattr__(self, "bundle_hash", expected_hash)

    def to_payload(self) -> dict[str, object]:
        return {
            "bundle": _encode_wire_value(self),
            "bundle_hash": self.bundle_hash,
            "format": _BUNDLE_FORMAT,
        }

    def to_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_payload())

    @classmethod
    def from_bytes(cls, payload: bytes) -> "LocalFactorMiningRunBundle":
        if not isinstance(payload, bytes):
            raise LocalFactorMiningRunBundleError("bundle artifact payload must be bytes")
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalFactorMiningRunBundleError("bundle artifact payload must be JSON") from exc
        if _canonical_json_bytes(decoded) != payload:
            raise LocalFactorMiningRunBundleError("bundle artifact payload must be canonical JSON")
        if not isinstance(decoded, dict) or set(decoded) != {"bundle", "bundle_hash", "format"}:
            raise LocalFactorMiningRunBundleError("bundle artifact payload has an unsupported shape")
        if decoded["format"] != _BUNDLE_FORMAT:
            raise LocalFactorMiningRunBundleError("bundle artifact format is unsupported")
        _reject_unsafe_wire_text(decoded)
        value = _decode_wire_value(decoded["bundle"])
        if type(value) is not cls:
            raise LocalFactorMiningRunBundleError("bundle artifact does not contain a run bundle")
        supplied_hash = _hash(decoded["bundle_hash"], "bundle_artifact.bundle_hash")
        if value.bundle_hash != supplied_hash:
            raise LocalFactorMiningRunBundleError("bundle artifact hash does not match its declaration")
        if value.to_bytes() != payload:
            raise LocalFactorMiningRunBundleError(
                "bundle artifact payload is not the canonical typed declaration"
            )
        return value


class GovernedResearchArtifactKind(str, Enum):
    """The finite output set for a completed local research run."""

    EXPOSURES = "exposures"
    WEIGHTS = "weights"
    ANALYSES = "analyses"
    DISCOVERY_EVIDENCE = "discovery_evidence"
    SELECTION_EVIDENCE = "selection_evidence"
    OOS_EVIDENCE = "oos_evidence"
    REPORT = "report"


@dataclass(frozen=True, slots=True)
class GovernedResearchArtifactReference:
    """A content- and lineage-addressed output published by one local run."""

    kind: GovernedResearchArtifactKind
    snapshot_hash: str
    content_hash: str
    lineage_snapshot_hash: str

    def __post_init__(self) -> None:
        if type(self.kind) is not GovernedResearchArtifactKind:
            raise LocalFactorMiningRunBundleError("artifact reference kind is unsupported")
        object.__setattr__(self, "snapshot_hash", _hash(self.snapshot_hash, "artifact.snapshot_hash"))
        object.__setattr__(self, "content_hash", _hash(self.content_hash, "artifact.content_hash"))
        object.__setattr__(
            self,
            "lineage_snapshot_hash",
            _hash(self.lineage_snapshot_hash, "artifact.lineage_snapshot_hash"),
        )


@dataclass(frozen=True, slots=True)
class LocalFactorMiningRunManifest:
    """Hash-only, research-only manifest for a published local campaign result."""

    bundle_hash: str
    dataset_version_hashes: tuple[str, ...]
    decision_replay_plan_hash: str
    campaign_hash: str
    generation_receipt_hash: str
    discovery_result_hash: str
    selection_commitment_hash: str
    oos_release_hash: str | None
    config_hash: str
    artifacts: tuple[GovernedResearchArtifactReference, ...]
    result_hash: str = ""
    manifest_hash: str = ""

    def __post_init__(self) -> None:
        bundle_hash = _hash(self.bundle_hash, "manifest.bundle_hash")
        datasets = _hashes(self.dataset_version_hashes, "manifest.dataset_version_hashes")
        plan_hash = _hash(self.decision_replay_plan_hash, "manifest.decision_replay_plan_hash")
        campaign_hash = _hash(self.campaign_hash, "manifest.campaign_hash")
        receipt_hash = _hash(self.generation_receipt_hash, "manifest.generation_receipt_hash")
        discovery_hash = _hash(self.discovery_result_hash, "manifest.discovery_result_hash")
        selection_hash = _hash(self.selection_commitment_hash, "manifest.selection_commitment_hash")
        oos_hash = (
            None
            if self.oos_release_hash is None
            else _hash(self.oos_release_hash, "manifest.oos_release_hash")
        )
        config_hash = _hash(self.config_hash, "manifest.config_hash")
        artifacts = tuple(self.artifacts)
        if not artifacts or not all(type(item) is GovernedResearchArtifactReference for item in artifacts):
            raise LocalFactorMiningRunBundleError("manifest artifacts must be exact governed references")
        if tuple(sorted(artifacts, key=lambda item: item.kind.value)) != artifacts:
            raise LocalFactorMiningRunBundleError("manifest artifacts must be sorted by kind")
        if len({item.kind for item in artifacts}) != len(artifacts):
            raise LocalFactorMiningRunBundleError("manifest artifacts cannot duplicate a kind")
        present = {item.kind for item in artifacts}
        required = {
            GovernedResearchArtifactKind.EXPOSURES,
            GovernedResearchArtifactKind.WEIGHTS,
            GovernedResearchArtifactKind.ANALYSES,
            GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,
            GovernedResearchArtifactKind.SELECTION_EVIDENCE,
            GovernedResearchArtifactKind.REPORT,
        }
        if not required.issubset(present):
            raise LocalFactorMiningRunBundleError("manifest is missing required research artifacts")
        if (GovernedResearchArtifactKind.OOS_EVIDENCE in present) != (oos_hash is not None):
            raise LocalFactorMiningRunBundleError(
                "manifest OOS artifact must exactly match OOS release availability"
            )
        result_hash = canonical_json_sha256(
            {
                "artifact_content_hashes": [item.content_hash for item in artifacts],
                "bundle_hash": bundle_hash,
                "campaign_hash": campaign_hash,
                "config_hash": config_hash,
                "dataset_version_hashes": list(datasets),
                "decision_replay_plan_hash": plan_hash,
                "discovery_result_hash": discovery_hash,
                "format": "northstar.local-factor-mining-run-result.v1",
                "generation_receipt_hash": receipt_hash,
                "oos_release_hash": oos_hash,
                "research_only": True,
                "selection_commitment_hash": selection_hash,
            }
        )
        manifest_hash = canonical_json_sha256(
            {
                "artifact_lineage_snapshot_hashes": [
                    item.lineage_snapshot_hash for item in artifacts
                ],
                "artifact_snapshot_hashes": [item.snapshot_hash for item in artifacts],
                "format": _MANIFEST_FORMAT,
                "result_hash": result_hash,
            }
        )
        supplied_result_hash = _optional_hash_or_empty(
            self.result_hash,
            "manifest.result_hash",
        )
        if supplied_result_hash is not None and supplied_result_hash != result_hash:
            raise LocalFactorMiningRunBundleError("manifest result_hash does not match inputs")
        supplied_manifest_hash = _optional_hash_or_empty(
            self.manifest_hash,
            "manifest.manifest_hash",
        )
        if supplied_manifest_hash is not None and supplied_manifest_hash != manifest_hash:
            raise LocalFactorMiningRunBundleError("manifest_hash does not match governed outputs")
        object.__setattr__(self, "bundle_hash", bundle_hash)
        object.__setattr__(self, "dataset_version_hashes", datasets)
        object.__setattr__(self, "decision_replay_plan_hash", plan_hash)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "generation_receipt_hash", receipt_hash)
        object.__setattr__(self, "discovery_result_hash", discovery_hash)
        object.__setattr__(self, "selection_commitment_hash", selection_hash)
        object.__setattr__(self, "oos_release_hash", oos_hash)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "result_hash", result_hash)
        object.__setattr__(self, "manifest_hash", manifest_hash)

    def to_payload(self) -> dict[str, object]:
        return {
            "format": _MANIFEST_FORMAT,
            "manifest": _encode_wire_value(self),
            "manifest_hash": self.manifest_hash,
        }

    def to_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_payload())

    @classmethod
    def from_bytes(cls, payload: bytes) -> "LocalFactorMiningRunManifest":
        if not isinstance(payload, bytes):
            raise LocalFactorMiningRunBundleError("manifest artifact payload must be bytes")
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalFactorMiningRunBundleError("manifest artifact payload must be JSON") from exc
        if _canonical_json_bytes(decoded) != payload:
            raise LocalFactorMiningRunBundleError("manifest artifact payload must be canonical JSON")
        if not isinstance(decoded, dict) or set(decoded) != {"format", "manifest", "manifest_hash"}:
            raise LocalFactorMiningRunBundleError("manifest artifact payload has an unsupported shape")
        if decoded["format"] != _MANIFEST_FORMAT:
            raise LocalFactorMiningRunBundleError("manifest artifact format is unsupported")
        value = _decode_wire_value(decoded["manifest"])
        if type(value) is not cls:
            raise LocalFactorMiningRunBundleError("artifact does not contain a local run manifest")
        supplied_hash = _hash(decoded["manifest_hash"], "manifest_artifact.manifest_hash")
        if value.manifest_hash != supplied_hash:
            raise LocalFactorMiningRunBundleError("manifest artifact hash does not match its content")
        if value.to_bytes() != payload:
            raise LocalFactorMiningRunBundleError(
                "manifest artifact payload is not the canonical typed declaration"
            )
        return value


@dataclass(frozen=True, slots=True)
class FactorResearchOOSRobustnessProof:
    """Reconstructable OOS robustness evidence retained with local analyses.

    This deliberately captures only the frozen research values required to
    prove the displayed OOS analysis/robustness conclusion.  It is not an
    application ``FactorResearchRun`` and cannot be used to run, admit, or
    trade anything.  Decoding reconstructs the typed robustness result so its
    conclusion and result hash are derived again from the frozen sub-results.
    """

    config: FactorPipelineConfig
    experiment: FactorResearchExperiment
    analyses: tuple[FactorAnalysisResult, ...]
    robustness: FactorRobustnessResult
    walk_forward: tuple[FactorWalkForwardResult, ...]
    manifest: FactorResearchRunManifest
    proof_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.config) is not FactorPipelineConfig:
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof config must be an exact FactorPipelineConfig"
            )
        if type(self.experiment) is not FactorResearchExperiment:
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof experiment must be an exact FactorResearchExperiment"
            )
        if type(self.robustness) is not FactorRobustnessResult:
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof robustness must be an exact FactorRobustnessResult"
            )
        if type(self.manifest) is not FactorResearchRunManifest:
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof manifest must be an exact FactorResearchRunManifest"
            )
        if not isinstance(self.analyses, tuple) or not self.analyses or not all(
            type(item) is FactorAnalysisResult for item in self.analyses
        ):
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof analyses must contain exact FactorAnalysisResult values"
            )
        analyses = self.analyses
        if (
            tuple(sorted(analyses, key=lambda item: item.factor_id)) != analyses
            or len({item.factor_id for item in analyses}) != len(analyses)
            or tuple(item.factor_id for item in analyses)
            != tuple(item.factor_id for item in self.config.alpha_factors)
        ):
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof analyses must exactly cover the configured alpha factors"
            )
        if not isinstance(self.walk_forward, tuple) or not self.walk_forward or not all(
            type(item) is FactorWalkForwardResult for item in self.walk_forward
        ):
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof walk_forward must contain exact FactorWalkForwardResult values"
            )
        walk_forward = self.walk_forward
        if (
            tuple(sorted(walk_forward, key=lambda item: item.fold_id)) != walk_forward
            or len({item.fold_id for item in walk_forward}) != len(walk_forward)
            or tuple(item.fold_id for item in walk_forward)
            != tuple(item.fold_id for item in self.config.walk_forward_folds)
        ):
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof walk_forward must exactly cover the configured folds"
            )
        config = self.config
        experiment = self.experiment
        robustness = self.robustness
        manifest = self.manifest
        if (
            robustness.config != config
            or robustness.experiment != experiment
            or robustness.plan != config.robustness_plan
            or experiment.config_hash != config.config_hash
            or experiment.code_revision != config.code_revision
        ):
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof config, experiment, and robustness must exactly bind"
            )
        if (
            manifest.config_hash != config.config_hash
            or manifest.feature_version_hashes != experiment.feature_version_hashes
            or manifest.code_revision != config.code_revision
            or manifest.decision_replay_plan_hash
            != experiment.decision_replay_plan_hash
            or manifest.experiment_hash != experiment.experiment_hash
            or manifest.dataset_version_hashes != experiment.dataset_version_hashes
            or manifest.checkpoint_data_hashes
            != tuple(sorted(robustness.checkpoint_data_hashes))
            or manifest.proposal_hashes != tuple(sorted(robustness.proposal_hashes))
            or manifest.analysis_hashes
            != tuple(sorted(item.analysis_hash for item in analyses))
            or manifest.robustness_plan_hash != robustness.plan_hash
            or manifest.robustness_result_hash != robustness.result_hash
            or manifest.walk_forward_result_hashes
            != tuple(sorted(item.result_hash for item in walk_forward))
        ):
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof manifest must exactly bind frozen OOS evidence"
            )
        proof_hash = canonical_json_sha256(
            {
                "analysis_hashes": list(manifest.analysis_hashes),
                "config_hash": config.config_hash,
                "experiment_hash": experiment.experiment_hash,
                "format": _OOS_ROBUSTNESS_PROOF_FORMAT,
                "manifest_hash": manifest.manifest_hash,
                "robustness_passed": robustness.passed,
                "robustness_result_hash": robustness.result_hash,
                "walk_forward_result_hashes": list(manifest.walk_forward_result_hashes),
            }
        )
        object.__setattr__(self, "proof_hash", proof_hash)


WireScalar: TypeAlias = None | bool | int | float | str
WireValue: TypeAlias = WireScalar | list["WireValue"] | dict[str, "WireValue"]


_WIRE_DATACLASS_TYPES: dict[str, type[object]] = {
    "DecisionReplayCheckpoint": DecisionReplayCheckpoint,
    "DecisionReplayPlan": DecisionReplayPlan,
    "FactorCandidateGenerationReceipt": FactorCandidateGenerationReceipt,
    "FactorCandidateProposal": FactorCandidateProposal,
    "FactorDefinition": FactorDefinition,
    "FactorMiningCampaignSpec": FactorMiningCampaignSpec,
    "FactorMiningCostScenario": FactorMiningCostScenario,
    "FactorMiningSelectionPolicy": FactorMiningSelectionPolicy,
    "FactorMiningRunnerResourceBudget": FactorMiningRunnerResourceBudget,
    "FactorParameterDomain": FactorParameterDomain,
    "FactorPipelineTemplate": FactorPipelineTemplate,
    "FactorRobustnessCostScenario": FactorRobustnessCostScenario,
    "FactorRobustnessParameterVariant": FactorRobustnessParameterVariant,
    "FactorRobustnessPlan": FactorRobustnessPlan,
    "FactorRobustnessSubperiod": FactorRobustnessSubperiod,
    "FactorStabilityThresholds": FactorStabilityThresholds,
    "FactorPrimitive": FactorPrimitive,
    "FactorSearchBudget": FactorSearchBudget,
    "GovernedResearchArtifactReference": GovernedResearchArtifactReference,
    "LocalFactorMiningRunBundle": LocalFactorMiningRunBundle,
    "LocalFactorMiningCampaignDeclaration": LocalFactorMiningCampaignDeclaration,
    "LocalFactorMiningRunConfig": LocalFactorMiningRunConfig,
    "LocalFactorMiningRunManifest": LocalFactorMiningRunManifest,
    "MarketDataPITSpec": MarketDataPITSpec,
    "ValidationPeriod": ValidationPeriod,
    "ValidationSplit": ValidationSplit,
    "WalkForwardFold": WalkForwardFold,
}
_WIRE_DATACLASS_NAMES: dict[type[object], str] = {
    value: key for key, value in _WIRE_DATACLASS_TYPES.items()
}
_OOS_PROOF_DATACLASS_TYPES: dict[str, type[object]] = {
    **_WIRE_DATACLASS_TYPES,
    "FactorAnalysisPeriod": FactorAnalysisPeriod,
    "FactorAnalysisResult": FactorAnalysisResult,
    "FactorPipelineConfig": FactorPipelineConfig,
    "FactorResearchExperiment": FactorResearchExperiment,
    "FactorResearchOOSRobustnessProof": FactorResearchOOSRobustnessProof,
    "FactorResearchRunManifest": FactorResearchRunManifest,
    "FactorRobustnessCostScenarioResult": FactorRobustnessCostScenarioResult,
    "FactorRobustnessFactorSummary": FactorRobustnessFactorSummary,
    "FactorRobustnessParameterVariantResult": FactorRobustnessParameterVariantResult,
    "FactorRobustnessResult": FactorRobustnessResult,
    "FactorRobustnessScenarioResult": FactorRobustnessScenarioResult,
    "FactorWalkForwardResult": FactorWalkForwardResult,
}
_OOS_PROOF_DATACLASS_NAMES: dict[type[object], str] = {
    value: key for key, value in _OOS_PROOF_DATACLASS_TYPES.items()
}
_WIRE_ENUM_TYPES: dict[str, type[Enum]] = {
    "FactorMiningMultipleTestingControl": FactorMiningMultipleTestingControl,
    "FactorMiningStageBoundaryMode": FactorMiningStageBoundaryMode,
    "FactorRole": FactorRole,
    "GovernedResearchArtifactKind": GovernedResearchArtifactKind,
    "MarketDataKind": MarketDataKind,
}
_WIRE_ENUM_NAMES: dict[type[Enum], str] = {value: key for key, value in _WIRE_ENUM_TYPES.items()}


def _encode_wire_value(
    value: object,
    *,
    dataclass_names: Mapping[type[object], str] = _WIRE_DATACLASS_NAMES,
    enum_names: Mapping[type[Enum], str] = _WIRE_ENUM_NAMES,
) -> WireValue:
    """Serialize only a closed set of declarative model values to canonical JSON."""

    if value is None or type(value) in {bool, int, str}:
        return cast(WireScalar, value)
    if type(value) is float:
        if not math.isfinite(cast(float, value)):
            raise LocalFactorMiningRunBundleError("wire payload cannot contain NaN or infinity")
        return cast(float, value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise LocalFactorMiningRunBundleError("wire datetime must be timezone-aware")
        return {"$datetime": value.astimezone(UTC).isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Enum):
        enum_name = enum_names.get(type(value))
        if enum_name is None:
            raise LocalFactorMiningRunBundleError("wire payload contains an unsupported enum")
        return {"$enum": enum_name, "value": cast(str, value.value)}
    if isinstance(value, tuple):
        return {
            "$tuple": [
                _encode_wire_value(
                    item,
                    dataclass_names=dataclass_names,
                    enum_names=enum_names,
                )
                for item in value
            ]
        }
    if is_dataclass(value) and not isinstance(value, type):
        dataclass_name = dataclass_names.get(type(value))
        if dataclass_name is None:
            raise LocalFactorMiningRunBundleError("wire payload contains an unsupported dataclass")
        return {
            "$type": dataclass_name,
            "fields": {
                item.name: _encode_wire_value(
                    getattr(value, item.name),
                    dataclass_names=dataclass_names,
                    enum_names=enum_names,
                )
                for item in fields(value)
                if item.init
            },
        }
    raise LocalFactorMiningRunBundleError("wire payload contains an unsupported value type")


def _decode_wire_value(
    value: object,
    *,
    dataclass_types: Mapping[str, type[object]] = _WIRE_DATACLASS_TYPES,
    enum_types: Mapping[str, type[Enum]] = _WIRE_ENUM_TYPES,
) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(cast(float, value)):
            raise LocalFactorMiningRunBundleError("wire payload cannot contain NaN or infinity")
        return value
    if not isinstance(value, dict):
        raise LocalFactorMiningRunBundleError("wire payload must use typed object envelopes")
    if set(value) == {"$datetime"}:
        raw = value["$datetime"]
        if not isinstance(raw, str):
            raise LocalFactorMiningRunBundleError("wire datetime must be text")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LocalFactorMiningRunBundleError("wire datetime is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise LocalFactorMiningRunBundleError("wire datetime must be timezone-aware")
        return parsed.astimezone(UTC)
    if set(value) == {"$date"}:
        raw = value["$date"]
        if not isinstance(raw, str):
            raise LocalFactorMiningRunBundleError("wire date must be text")
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise LocalFactorMiningRunBundleError("wire date is invalid") from exc
    if set(value) == {"$tuple"}:
        raw_values = value["$tuple"]
        if not isinstance(raw_values, list):
            raise LocalFactorMiningRunBundleError("wire tuple must be a list")
        return tuple(
            _decode_wire_value(
                item,
                dataclass_types=dataclass_types,
                enum_types=enum_types,
            )
            for item in raw_values
        )
    if set(value) == {"$enum", "value"}:
        enum_name = value["$enum"]
        raw_value = value["value"]
        if not isinstance(enum_name, str) or not isinstance(raw_value, str):
            raise LocalFactorMiningRunBundleError("wire enum has an invalid shape")
        enum_type = enum_types.get(enum_name)
        if enum_type is None:
            raise LocalFactorMiningRunBundleError("wire enum is not allowlisted")
        try:
            return enum_type(raw_value)
        except ValueError as exc:
            raise LocalFactorMiningRunBundleError("wire enum value is invalid") from exc
    if set(value) == {"$type", "fields"}:
        type_name = value["$type"]
        raw_fields = value["fields"]
        if not isinstance(type_name, str) or not isinstance(raw_fields, dict):
            raise LocalFactorMiningRunBundleError("wire dataclass has an invalid shape")
        dataclass_type = dataclass_types.get(type_name)
        if dataclass_type is None:
            raise LocalFactorMiningRunBundleError("wire dataclass is not allowlisted")
        expected = {
            item.name for item in fields(cast(Any, dataclass_type)) if item.init
        }
        if set(raw_fields) != expected:
            raise LocalFactorMiningRunBundleError("wire dataclass fields do not match its contract")
        decoded_fields = {
            name: _decode_wire_value(
                raw_fields[name],
                dataclass_types=dataclass_types,
                enum_types=enum_types,
            )
            for name in sorted(raw_fields)
        }
        constructor = cast(Callable[..., object], dataclass_type)
        try:
            return constructor(**decoded_fields)
        except (TypeError, ValueError) as exc:
            raise LocalFactorMiningRunBundleError(
                "wire dataclass cannot be reconstructed safely"
            ) from exc
    raise LocalFactorMiningRunBundleError("wire payload envelope is unsupported")


def encode_factor_research_oos_robustness_proof(
    proof: FactorResearchOOSRobustnessProof,
) -> dict[str, object]:
    """Encode one closed OOS proof without widening the input-bundle codec."""

    if type(proof) is not FactorResearchOOSRobustnessProof:
        raise LocalFactorMiningRunBundleError(
            "OOS robustness proof must be an exact FactorResearchOOSRobustnessProof"
        )
    return {
        "format": _OOS_ROBUSTNESS_PROOF_FORMAT,
        "proof": _encode_wire_value(
            proof,
            dataclass_names=_OOS_PROOF_DATACLASS_NAMES,
            enum_names=_WIRE_ENUM_NAMES,
        ),
        "proof_hash": proof.proof_hash,
    }


def decode_factor_research_oos_robustness_proof(
    payload: object,
) -> FactorResearchOOSRobustnessProof:
    """Decode only a canonical proof envelope and re-derive typed result fields."""

    if not isinstance(payload, dict) or set(payload) != {
        "format",
        "proof",
        "proof_hash",
    }:
        raise LocalFactorMiningRunBundleError(
            "OOS robustness proof payload has an unsupported shape"
        )
    if payload["format"] != _OOS_ROBUSTNESS_PROOF_FORMAT:
        raise LocalFactorMiningRunBundleError("OOS robustness proof format is unsupported")
    _reject_unsafe_wire_text(payload)
    value = _decode_wire_value(
        payload["proof"],
        dataclass_types=_OOS_PROOF_DATACLASS_TYPES,
        enum_types=_WIRE_ENUM_TYPES,
    )
    if type(value) is not FactorResearchOOSRobustnessProof:
        raise LocalFactorMiningRunBundleError(
            "OOS robustness proof does not contain a typed proof"
        )
    supplied_hash = _hash(payload["proof_hash"], "oos_robustness_proof.proof_hash")
    if value.proof_hash != supplied_hash:
        raise LocalFactorMiningRunBundleError(
            "OOS robustness proof hash does not match reconstructed evidence"
        )
    if encode_factor_research_oos_robustness_proof(value) != payload:
        raise LocalFactorMiningRunBundleError(
            "OOS robustness proof is not the canonical typed evidence envelope"
        )
    return value


def project_factor_research_oos_robustness_proof(
    proof: FactorResearchOOSRobustnessProof,
) -> dict[str, object]:
    """Return the sole canonical display projection for an OOS proof."""

    if type(proof) is not FactorResearchOOSRobustnessProof:
        raise LocalFactorMiningRunBundleError(
            "OOS robustness proof must be exact before it can be projected"
        )
    manifest = proof.manifest
    return {
        "analyses": _proof_plain(proof.analyses),
        "lookahead_certificate_hash": manifest.lookahead_certificate_hash,
        "robustness": _proof_robustness_mapping(proof.robustness),
        "run_manifest": {
            "config_hash": manifest.config_hash,
            "manifest_hash": manifest.manifest_hash,
            "robustness_plan_hash": manifest.robustness_plan_hash,
            "robustness_result_hash": manifest.robustness_result_hash,
        },
        "walk_forward": _proof_plain(proof.walk_forward),
    }


def _proof_robustness_mapping(value: FactorRobustnessResult) -> dict[str, object]:
    return {
        "config_hash": value.config_hash,
        "cost_scenario_results": _proof_plain(value.cost_scenario_results),
        "factor_summaries": _proof_plain(value.factor_summaries),
        "parameter_variant_results": _proof_plain(value.parameter_variant_results),
        "passed": value.passed,
        "plan_hash": value.plan_hash,
        "result_hash": value.result_hash,
        "scenario_results": _proof_plain(value.scenario_results),
    }


def _proof_plain(value: object) -> object:
    """Canonical JSON-safe presentation view of one typed proof member."""

    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        numeric = cast(float, value)
        if not math.isfinite(numeric):
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof contains a non-finite number"
            )
        return numeric
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise LocalFactorMiningRunBundleError(
                "OOS robustness proof contains a naive datetime"
            )
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_proof_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _proof_plain(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _proof_plain(getattr(value, item.name)) for item in fields(value)}
    raise LocalFactorMiningRunBundleError(
        "OOS robustness proof includes an unsupported runtime value"
    )


def _reject_unsafe_wire_text(value: object) -> None:
    """Reject ambiguity before dataclass construction, including ``latest`` and paths."""

    if isinstance(value, str):
        if value.casefold() == "latest" or value.startswith(("/", "\\", "~/", "~\\")):
            raise LocalFactorMiningRunBundleError(
                "bundle artifact cannot contain latest selectors or local paths"
            )
        if _ABSOLUTE_PATH_RE.match(value):
            raise LocalFactorMiningRunBundleError(
                "bundle artifact cannot contain latest selectors or local paths"
            )
        return
    if isinstance(value, list):
        for item in value:
            _reject_unsafe_wire_text(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_unsafe_wire_text(item)
