"""Trusted composition root for one sealed factor-mining campaign.

This module, rather than the AI-facing agent, owns ArtifactStore access,
DecisionReplayPlan binding, and FactorResearchPipeline invocation.  It executes
only candidates that already passed the pure research-domain validator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
import math

from northstar_quant.application.factor_mining_tools import (
    EvaluateFactorCandidateDiscoveryBatchRequest,
)
from northstar_quant.application.factor_research import (
    FactorResearchDiscoveryReplay,
    FactorResearchPipeline,
    FactorResearchRun,
)
from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.research.features.canonical import CN_FUTURES_FEATURE_BAR_V1
from northstar_quant.research.features.catalog import get_canonical_feature_registration
from northstar_quant.research.factor_mining.models import (
    CandidateValidationStatus,
    FactorCandidateValidation,
    FactorMiningCampaignSpec,
    FactorMiningError,
)
from northstar_quant.research.factor_mining.evaluation import (
    FactorMiningEvaluationError,
    build_discovery_result,
    build_oos_release_result,
    rejected_discovery_result,
    validate_global_discovery_oos_layout,
)
from northstar_quant.research.factor_mining.protocol import (
    FactorCandidateDiscoveryResult,
    FactorMiningDiscoveryResult,
    FactorMiningOOSRelease,
    FactorMiningSelectionCommitment,
    select_discovery_candidates,
)
from northstar_quant.research.factor_mining.validator import validate_factor_candidate
from northstar_quant.research.validation.lookahead import DecisionReplayPlan


__all__ = [
    "FactorMiningCampaignArtifactMaterial",
    "FactorMiningCampaignError",
    "FactorMiningCampaignRunner",
    "FactorMiningCandidateArtifactMaterial",
]


class FactorMiningCampaignError(ValueError):
    """Raised when a sealed campaign cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class _DiscoveryRecord:
    validation: FactorCandidateValidation
    pipeline_config_hash: str
    discovery_replay: FactorResearchDiscoveryReplay


@dataclass(frozen=True, slots=True)
class FactorMiningCandidateArtifactMaterial:
    """Trusted local evidence retained for governed artifact publication only.

    This is intentionally outside the AI tool facade.  It never changes a
    research result, admits a candidate, or creates any trading object.
    """

    candidate_id: str
    discovery_replay: FactorResearchDiscoveryReplay
    oos_run: FactorResearchRun | None


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignArtifactMaterial:
    """Exact discovery/OOS material behind one in-memory campaign receipt."""

    discovery: FactorMiningDiscoveryResult
    commitment: FactorMiningSelectionCommitment
    release: FactorMiningOOSRelease | None
    candidates: tuple[FactorMiningCandidateArtifactMaterial, ...]


class FactorMiningCampaignRunner:
    """Run discovery, commit selection, then explicitly release OOS exactly once.

    The AI-facing tool can call only ``evaluate_discovery_candidate_batch``.
    Selection and OOS release are trusted local researcher capabilities and are
    intentionally absent from that facade.
    """

    __slots__ = (
        "_artifact_store",
        "_campaign",
        "_plan",
        "_seen_generation_receipt_hashes",
        "_discoveries",
        "_records_by_discovery_hash",
        "_commitments",
        "_released_commitment_hashes",
        "_oos_runs_by_release_hash",
    )

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        campaign: FactorMiningCampaignSpec,
        plan: DecisionReplayPlan,
    ) -> None:
        if type(artifact_store) is not ArtifactStore:
            raise FactorMiningCampaignError("artifact_store must be an exact ArtifactStore")
        if type(campaign) is not FactorMiningCampaignSpec:
            raise FactorMiningCampaignError("campaign must be an exact FactorMiningCampaignSpec")
        if type(plan) is not DecisionReplayPlan:
            raise FactorMiningCampaignError("plan must be an exact DecisionReplayPlan")
        _preflight_canonical_policy(campaign)
        plan_datasets = tuple(sorted({item.dataset_version_hash for item in plan.checkpoints}))
        if campaign.decision_replay_plan_hash != plan.schedule_hash:
            raise FactorMiningCampaignError("campaign is not bound to this DecisionReplayPlan")
        if campaign.dataset_version_hashes != plan_datasets:
            raise FactorMiningCampaignError("campaign datasets do not exactly bind the replay plan")
        try:
            validate_global_discovery_oos_layout(campaign)
        except FactorMiningEvaluationError as exc:
            raise FactorMiningCampaignError(str(exc)) from exc
        earliest_oos_decision_at = _earliest_oos_decision_at(plan, campaign)
        if campaign.selection_at >= earliest_oos_decision_at:
            raise FactorMiningCampaignError(
                "campaign selection_at must be strictly before the earliest OOS decision"
            )
        _validate_discovery_outcome_maturity(plan, campaign)
        self._artifact_store = artifact_store
        self._campaign = campaign
        self._plan = plan
        self._seen_generation_receipt_hashes: set[str] = set()
        self._discoveries: dict[str, FactorMiningDiscoveryResult] = {}
        self._records_by_discovery_hash: dict[str, dict[str, _DiscoveryRecord]] = {}
        self._commitments: dict[str, FactorMiningSelectionCommitment] = {}
        self._released_commitment_hashes: set[str] = set()
        self._oos_runs_by_release_hash: dict[str, dict[str, FactorResearchRun]] = {}

    def evaluate_discovery_candidate_batch(
        self,
        *,
        request: EvaluateFactorCandidateDiscoveryBatchRequest,
    ) -> FactorMiningDiscoveryResult:
        """Evaluate one receipt through IS/validation only, without materializing OOS."""

        if type(request) is not EvaluateFactorCandidateDiscoveryBatchRequest:
            raise FactorMiningCampaignError(
                "request must be an exact EvaluateFactorCandidateDiscoveryBatchRequest"
            )
        generation = request.generation
        try:
            generation.require_campaign(self._campaign)
        except FactorMiningError as exc:
            raise FactorMiningCampaignError(
                "generation receipt does not match the sealed campaign"
            ) from exc
        if generation.receipt_hash in self._seen_generation_receipt_hashes:
            raise FactorMiningCampaignError(
                "a generation receipt cannot be automatically replayed or retried"
            )
        # Reserve before work begins.  A partial failure cannot be treated as a
        # proof that an external/compute action did not occur.
        self._seen_generation_receipt_hashes.add(generation.receipt_hash)

        results: list[FactorCandidateDiscoveryResult] = []
        records: dict[str, _DiscoveryRecord] = {}
        evaluated_definition_hashes: set[str] = set()
        for candidate in generation.proposals:
            validation = validate_factor_candidate(campaign=self._campaign, candidate=candidate)
            if validation.status is CandidateValidationStatus.REJECTED:
                results.append(rejected_discovery_result(campaign=self._campaign, validation=validation))
                continue
            definition = validation.factor_definition
            if definition is None:
                raise FactorMiningCampaignError("validated candidate lost its factor definition")
            if definition.definition_hash in evaluated_definition_hashes:
                results.append(
                    rejected_discovery_result(
                        campaign=self._campaign,
                        validation=validation,
                        reason_code="DUPLICATE_FACTOR_DEFINITION",
                    )
                )
                continue
            evaluated_definition_hashes.add(definition.definition_hash)
            config = self._campaign.template.build_config(
                campaign_id=self._campaign.campaign_id,
                candidate_id=candidate.candidate_id,
                factor_definition=definition,
            )
            discovery_replay = FactorResearchPipeline(
                artifact_store=self._artifact_store,
                config=config,
            ).run_discovery(plan=self._plan, selection_at=self._campaign.selection_at)
            if not discovery_replay.research_only:
                raise FactorMiningCampaignError("discovery replay left the research-only boundary")
            result = build_discovery_result(
                campaign=self._campaign,
                validation=validation,
                config=config,
                checkpoint_data=discovery_replay.checkpoint_data,
                proposals=discovery_replay.proposals,
                outcomes=discovery_replay.outcomes,
                discovery_replay_hash=discovery_replay.replay_hash,
            )
            results.append(result)
            records[candidate.candidate_id] = _DiscoveryRecord(
                validation=validation,
                pipeline_config_hash=config.config_hash,
                discovery_replay=discovery_replay,
            )
        discovery = FactorMiningDiscoveryResult(
            campaign_id=self._campaign.campaign_id,
            campaign_hash=self._campaign.campaign_hash,
            generation_receipt_hash=generation.receipt_hash,
            selection_policy_hash=self._campaign.selection_policy.policy_hash,
            results=tuple(sorted(results, key=lambda item: item.candidate_id)),
        )
        self._discoveries[discovery.discovery_result_hash] = discovery
        self._records_by_discovery_hash[discovery.discovery_result_hash] = records
        return discovery

    def commit_selection(
        self,
        *,
        discovery: FactorMiningDiscoveryResult,
    ) -> FactorMiningSelectionCommitment:
        """Create the deterministic selection commitment without running OOS."""

        if type(discovery) is not FactorMiningDiscoveryResult:
            raise FactorMiningCampaignError("discovery must be an exact FactorMiningDiscoveryResult")
        stored = self._discoveries.get(discovery.discovery_result_hash)
        if stored != discovery:
            raise FactorMiningCampaignError(
                "discovery state is unavailable in this local runner; fail closed rather than replay"
            )
        commitment = select_discovery_candidates(campaign=self._campaign, discovery=discovery)
        existing = self._commitments.get(commitment.commitment_hash)
        if existing is not None and existing != commitment:
            raise FactorMiningCampaignError("selection commitment hash collision")
        self._commitments[commitment.commitment_hash] = commitment
        return commitment

    def release_oos(
        self,
        *,
        commitment: FactorMiningSelectionCommitment,
    ) -> FactorMiningOOSRelease:
        """Explicitly run and reveal OOS evidence once for the committed subset.

        The reservation is made before work.  If a local process fails partway
        through, the status is intentionally unresolved and this in-memory
        runner refuses a silent replay; PostgreSQL durability is P11-WP05.
        """

        if type(commitment) is not FactorMiningSelectionCommitment:
            raise FactorMiningCampaignError("commitment must be an exact FactorMiningSelectionCommitment")
        stored = self._commitments.get(commitment.commitment_hash)
        if stored != commitment:
            raise FactorMiningCampaignError(
                "selection commitment is unavailable in this local runner; fail closed rather than replay"
            )
        selected = commitment.selected_records
        if not selected:
            raise FactorMiningCampaignError("an empty selection commitment cannot release OOS")
        if commitment.commitment_hash in self._released_commitment_hashes:
            raise FactorMiningCampaignError("a selection commitment cannot release OOS more than once")
        self._released_commitment_hashes.add(commitment.commitment_hash)
        try:
            records = self._records_by_discovery_hash[commitment.discovery_result_hash]
        except KeyError as exc:  # pragma: no cover - commit_selection guards this map.
            raise FactorMiningCampaignError("discovery record state is unavailable") from exc
        released = []
        full_runs: dict[str, FactorResearchRun] = {}
        for selection_record in selected:
            try:
                record = records[selection_record.candidate_id]
            except KeyError as exc:  # pragma: no cover - discovery contracts guard this map.
                raise FactorMiningCampaignError("selected candidate record is unavailable") from exc
            validation = record.validation
            if validation.factor_definition is None:  # pragma: no cover - validated records guard this.
                raise FactorMiningCampaignError("selected candidate lost its factor definition")
            config = self._campaign.template.build_config(
                campaign_id=self._campaign.campaign_id,
                candidate_id=validation.candidate.candidate_id,
                factor_definition=validation.factor_definition,
            )
            if config.config_hash != record.pipeline_config_hash:
                raise FactorMiningCampaignError("selected candidate pipeline configuration changed")
            factor_run = FactorResearchPipeline(
                artifact_store=self._artifact_store,
                config=config,
            ).run(plan=self._plan)
            if not (
                factor_run.research_only
                and not factor_run.candidate_admission_eligible
                and not factor_run.simnow_handoff_allowed
            ):
                raise FactorMiningCampaignError("factor pipeline result left the research-only boundary")
            full_runs[validation.candidate.candidate_id] = factor_run
            released.append(
                build_oos_release_result(
                    campaign=self._campaign,
                    selection_record=selection_record,
                    validation=validation,
                    config=config,
                    checkpoint_data=factor_run.checkpoint_data,
                    proposals=factor_run.proposals,
                    outcomes=factor_run.outcomes,
                    run_manifest_hash=factor_run.manifest.manifest_hash,
                    lookahead_certificate_hash=factor_run.lookahead_certificate.certificate_hash,
                )
            )
        release = FactorMiningOOSRelease(
            campaign_id=self._campaign.campaign_id,
            campaign_hash=self._campaign.campaign_hash,
            selection_commitment_hash=commitment.commitment_hash,
            results=tuple(sorted(released, key=lambda item: item.candidate_id)),
        )
        self._oos_runs_by_release_hash[release.release_hash] = full_runs
        return release

    def collect_research_artifact_material(
        self,
        *,
        discovery: FactorMiningDiscoveryResult,
        commitment: FactorMiningSelectionCommitment,
        release: FactorMiningOOSRelease | None,
    ) -> FactorMiningCampaignArtifactMaterial:
        """Return exact retained evidence for immutable local artifact publication.

        The method refuses to recreate a discovery/OOS run after process loss:
        that durability boundary belongs to P11-WP05.  It is deliberately not
        part of ``FactorMiningCampaignPort`` or the AI-visible tool surface.
        """

        if type(discovery) is not FactorMiningDiscoveryResult:
            raise FactorMiningCampaignError("discovery must be an exact FactorMiningDiscoveryResult")
        if type(commitment) is not FactorMiningSelectionCommitment:
            raise FactorMiningCampaignError(
                "commitment must be an exact FactorMiningSelectionCommitment"
            )
        stored_discovery = self._discoveries.get(discovery.discovery_result_hash)
        stored_commitment = self._commitments.get(commitment.commitment_hash)
        if stored_discovery != discovery or stored_commitment != commitment:
            raise FactorMiningCampaignError(
                "campaign evidence is unavailable in this local runner; fail closed rather than replay"
            )
        if commitment.discovery_result_hash != discovery.discovery_result_hash:
            raise FactorMiningCampaignError("selection commitment does not bind discovery")
        selected_ids = {item.candidate_id for item in commitment.selected_records}
        oos_runs: dict[str, FactorResearchRun] = {}
        if release is None:
            if selected_ids:
                raise FactorMiningCampaignError(
                    "selected candidates require an explicit OOS release before artifact publication"
                )
        else:
            if type(release) is not FactorMiningOOSRelease:
                raise FactorMiningCampaignError("release must be an exact FactorMiningOOSRelease")
            if release.selection_commitment_hash != commitment.commitment_hash:
                raise FactorMiningCampaignError("OOS release does not bind the selection commitment")
            stored_runs = self._oos_runs_by_release_hash.get(release.release_hash)
            if stored_runs is None or set(stored_runs) != selected_ids:
                raise FactorMiningCampaignError(
                    "OOS artifact material is unavailable in this local runner; fail closed rather than replay"
                )
            if {item.candidate_id for item in release.results} != selected_ids:
                raise FactorMiningCampaignError("OOS release results do not match selected candidates")
            oos_runs = stored_runs
        records = self._records_by_discovery_hash[discovery.discovery_result_hash]
        candidates = tuple(
            FactorMiningCandidateArtifactMaterial(
                candidate_id=candidate_id,
                discovery_replay=record.discovery_replay,
                oos_run=oos_runs.get(candidate_id),
            )
            for candidate_id, record in sorted(records.items())
        )
        return FactorMiningCampaignArtifactMaterial(
            discovery=discovery,
            commitment=commitment,
            release=release,
            candidates=candidates,
        )


def _earliest_oos_decision_at(
    plan: DecisionReplayPlan,
    campaign: FactorMiningCampaignSpec,
) -> datetime:
    """Return the first plan decision in a frozen OOS fold, or fail closed."""

    decision_times = tuple(
        checkpoint.decision_at
        for checkpoint in plan.checkpoints
        if any(
            fold.split.out_of_sample.start
            <= _event_date(checkpoint.decision_event_time)
            <= fold.split.out_of_sample.end
            for fold in campaign.template.walk_forward_folds
        )
    )
    if not decision_times:
        raise FactorMiningCampaignError(
            "replay plan has no checkpoint inside the campaign's frozen OOS periods"
        )
    return min(decision_times)


def _validate_discovery_outcome_maturity(
    plan: DecisionReplayPlan,
    campaign: FactorMiningCampaignSpec,
) -> None:
    """Require all retained development outcomes to be known before selection.

    A stage contains an outcome only when both its origin and evaluation
    checkpoint fall within that same stage.  Outcomes that cross a declared
    IS/validation/OOS boundary are deliberately purged by the stage evaluator,
    so they neither become development evidence nor leak into another stage.
    This preflight uses only the sealed replay schedule; it never materializes
    an OOS feature, factor, target, or return.
    """

    shared_split = campaign.template.walk_forward_folds[0].split
    development_periods = (shared_split.in_sample, shared_split.validation)
    holding_period_sessions = campaign.template.holding_period_sessions
    checkpoints = plan.checkpoints
    for origin_index, origin in enumerate(checkpoints):
        origin_session = _event_date(origin.decision_event_time)
        period = next(
            (item for item in development_periods if item.contains(origin_session)),
            None,
        )
        if period is None:
            continue
        evaluation_index = origin_index + holding_period_sessions
        if evaluation_index >= len(checkpoints):
            # The last incomplete forward observation is not usable evidence;
            # it is intentionally absent from the stage ledger.
            continue
        evaluation = checkpoints[evaluation_index]
        if not period.contains(_event_date(evaluation.decision_event_time)):
            continue
        if evaluation.decision_at >= campaign.selection_at:
            raise FactorMiningCampaignError(
                "campaign selection_at must be strictly after all discovery outcome evaluations"
            )


def _event_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _preflight_canonical_policy(campaign: FactorMiningCampaignSpec) -> None:
    """Reject an unusable host policy while composing the trusted campaign port.

    Production composition must construct this runner before binding it into
    FactorMiningToolApi.  Thus the provider cannot be called until this
    preflight has accepted the exact canonical daily-bar policy.
    """

    _preflight_feature_parameters(
        feature_id=campaign.template.risk_model_factor.feature_id,
        parameters=campaign.template.risk_model_factor.parameters,
        subject="campaign template risk_model_factor",
    )
    for primitive in campaign.primitives:
        schema = _canonical_parameter_schema(
            feature_id=primitive.feature_id,
            subject=f"campaign primitive {primitive.primitive_id}",
        )
        domains = {domain.name: domain for domain in primitive.parameter_domains}
        if set(domains) != set(schema):
            raise FactorMiningCampaignError(
                f"campaign primitive {primitive.primitive_id} parameter domains "
                "must exactly match the canonical feature schema"
            )
        for parameter_name, domain in domains.items():
            rule = schema[parameter_name]
            for value in domain.allowed_values:
                _validate_canonical_parameter(
                    value=value,
                    rule=rule,
                    subject=(
                        f"campaign primitive {primitive.primitive_id}."
                        f"{parameter_name}"
                    ),
                )


def _preflight_feature_parameters(
    *,
    feature_id: str,
    parameters: Mapping[str, object],
    subject: str,
) -> None:
    schema = _canonical_parameter_schema(feature_id=feature_id, subject=subject)
    if set(parameters) != set(schema):
        raise FactorMiningCampaignError(
            f"{subject} parameters must exactly match the canonical feature schema"
        )
    for parameter_name, value in parameters.items():
        _validate_canonical_parameter(
            value=value,
            rule=schema[parameter_name],
            subject=f"{subject}.{parameter_name}",
        )


def _canonical_parameter_schema(*, feature_id: str, subject: str) -> Mapping[str, object]:
    try:
        definition = get_canonical_feature_registration(feature_id).definition
    except KeyError as exc:
        raise FactorMiningCampaignError(
            f"{subject} must reference a known canonical feature"
        ) from exc
    if definition.input_contract != CN_FUTURES_FEATURE_BAR_V1:
        raise FactorMiningCampaignError(
            f"{subject} must use the exact continuous daily feature-bar input contract"
        )
    return definition.parameter_schema


def _finite_numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _validate_canonical_parameter(*, value: object, rule: object, subject: str) -> None:
    if not isinstance(rule, Mapping):  # pragma: no cover - protected catalog contract.
        raise FactorMiningCampaignError(f"{subject} canonical parameter rule is invalid")
    kind = rule.get("type")
    numeric_value = _finite_numeric(value)
    if kind == "integer":
        valid = type(value) is int
    elif kind == "number":
        valid = numeric_value is not None
    elif kind == "string":
        valid = type(value) is str
    elif kind == "boolean":
        valid = type(value) is bool
    else:  # pragma: no cover - protected catalog contract.
        raise FactorMiningCampaignError(f"{subject} canonical parameter type is invalid")
    if not valid:
        raise FactorMiningCampaignError(
            f"{subject} does not satisfy the canonical parameter type"
        )
    if kind in {"integer", "number"}:
        if numeric_value is None:  # pragma: no cover - guarded by the type validation above.
            raise FactorMiningCampaignError(f"{subject} must be a finite number")
        numeric = numeric_value
        minimum = rule.get("minimum")
        maximum = rule.get("maximum")
        if isinstance(minimum, (int, float)) and numeric < float(minimum):
            raise FactorMiningCampaignError(f"{subject} is below the canonical minimum")
        if isinstance(maximum, (int, float)) and numeric > float(maximum):
            raise FactorMiningCampaignError(f"{subject} is above the canonical maximum")
    allowed_values = rule.get("allowed_values")
    if isinstance(allowed_values, (list, tuple)) and value not in allowed_values:
        raise FactorMiningCampaignError(f"{subject} is outside canonical allowed_values")
