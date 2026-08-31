"""Pure factor-mining stage evaluation below the application composition root.

The module receives already materialized PIT checkpoint objects.  It has no
ArtifactStore, provider, filesystem, database, portfolio, or execution
capability.  Its only job is to turn frozen IS/validation/OOS periods into
hash-bound research evidence under the declared flat-start/forced-close cost
boundary.
"""

from __future__ import annotations

import polars as pl

from northstar_quant.research.backtest.event_engine import run_event_backtest
from northstar_quant.research.factor_mining.models import (
    CandidateValidationStatus,
    FactorCandidateValidation,
    FactorMiningCampaignSpec,
    FactorMiningError,
    _require_global_discovery_oos_layout,
)
from northstar_quant.research.factor_mining.protocol import (
    CandidateDiscoveryDisposition,
    FactorCandidateDiscoveryResult,
    FactorDiscoveryStageCostResult,
    FactorMiningOOSReleaseResult,
    FactorMiningSelectionRecord,
    FactorMiningStageEvidence,
)
from northstar_quant.research.factors.analysis import analyze_factor
from northstar_quant.research.factors.frames import (
    build_factor_market_frame,
    build_factor_target_frame,
)
from northstar_quant.research.factors.models import (
    FactorCheckpointData,
    FactorForwardOutcome,
    FactorPipelineConfig,
    FactorPortfolioProposal,
    FactorResearchError,
)
from northstar_quant.research.validation.framework import ValidationPeriod, ValidationStage


__all__ = [
    "FactorMiningEvaluationError",
    "build_discovery_result",
    "build_oos_release_result",
    "rejected_discovery_result",
    "validate_global_discovery_oos_layout",
]


class FactorMiningEvaluationError(FactorMiningError):
    """Raised when the sealed stage layout cannot yield safe research evidence."""


def validate_global_discovery_oos_layout(campaign: FactorMiningCampaignSpec) -> None:
    """Require one globally isolated discovery segment before every OOS fold.

    Rolling re-training that lets one fold's OOS become a later fold's IS is a
    valid research pattern in a different protocol, but it cannot support a
    single once-only OOS release.  The first local protocol therefore uses one
    shared IS/validation declaration followed by two or more OOS folds.
    """

    if type(campaign) is not FactorMiningCampaignSpec:
        raise FactorMiningEvaluationError("campaign must be an exact FactorMiningCampaignSpec")
    try:
        _require_global_discovery_oos_layout(campaign.template)
    except FactorMiningError as exc:
        raise FactorMiningEvaluationError(str(exc)) from exc


def rejected_discovery_result(
    *,
    campaign: FactorMiningCampaignSpec,
    validation: FactorCandidateValidation,
    reason_code: str | None = None,
) -> FactorCandidateDiscoveryResult:
    """Project a reason-coded pre-evaluation rejection without OOS evidence."""

    if type(campaign) is not FactorMiningCampaignSpec:
        raise FactorMiningEvaluationError("campaign must be an exact FactorMiningCampaignSpec")
    if type(validation) is not FactorCandidateValidation:
        raise FactorMiningEvaluationError("validation must be an exact FactorCandidateValidation")
    return FactorCandidateDiscoveryResult(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate_id=validation.candidate.candidate_id,
        candidate_hash=validation.candidate.candidate_hash,
        disposition=CandidateDiscoveryDisposition.REJECTED_INPUT,
        reason_code=reason_code or validation.reason_code,
        validation_hash=validation.validation_hash,
    )


def build_discovery_result(
    *,
    campaign: FactorMiningCampaignSpec,
    validation: FactorCandidateValidation,
    config: FactorPipelineConfig,
    checkpoint_data: tuple[FactorCheckpointData, ...],
    proposals: tuple[FactorPortfolioProposal, ...],
    outcomes: tuple[FactorForwardOutcome, ...],
    discovery_replay_hash: str,
) -> FactorCandidateDiscoveryResult:
    """Build evidence from shared IS/validation data only.

    Cross-boundary forward outcomes are purged rather than assigned to the
    neighboring stage.  Every retained discovery outcome must mature strictly
    before ``campaign.selection_at``.
    """

    _require_validated_candidate(campaign=campaign, validation=validation, config=config)
    validate_global_discovery_oos_layout(campaign)
    factor_definition = validation.factor_definition
    if factor_definition is None:  # pragma: no cover - _require_validated_candidate guards this.
        raise FactorMiningEvaluationError("validated candidate lost its factor definition")
    try:
        shared_split = campaign.template.walk_forward_folds[0].split
        stages = tuple(
            _build_stage_evidence(
                campaign=campaign,
                validation=validation,
                config=config,
                checkpoint_data=checkpoint_data,
                proposals=proposals,
                outcomes=outcomes,
                fold_id=campaign.template.walk_forward_folds[0].fold_id,
                fold_hash=campaign.template.walk_forward_folds[0].fold_hash,
                stage=stage,
                period=shared_split.period_for(stage),
                selection_bound=True,
            )
            for stage in (ValidationStage.IN_SAMPLE, ValidationStage.VALIDATION)
        )
    except (FactorResearchError, ValueError):
        return FactorCandidateDiscoveryResult(
            campaign_id=campaign.campaign_id,
            campaign_hash=campaign.campaign_hash,
            candidate_id=validation.candidate.candidate_id,
            candidate_hash=validation.candidate.candidate_hash,
            disposition=CandidateDiscoveryDisposition.REJECTED_DISCOVERY,
            reason_code="DISCOVERY_STAGE_EVIDENCE_UNAVAILABLE",
            validation_hash=validation.validation_hash,
            factor_definition_hash=factor_definition.definition_hash,
            pipeline_config_hash=config.config_hash,
            discovery_replay_hash=discovery_replay_hash,
        )
    return FactorCandidateDiscoveryResult(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate_id=validation.candidate.candidate_id,
        candidate_hash=validation.candidate.candidate_hash,
        disposition=CandidateDiscoveryDisposition.DISCOVERY_EVALUATED,
        reason_code="DISCOVERY_EVALUATED",
        validation_hash=validation.validation_hash,
        factor_definition_hash=factor_definition.definition_hash,
        pipeline_config_hash=config.config_hash,
        discovery_replay_hash=discovery_replay_hash,
        stage_evidence=stages,
    )


def build_oos_release_result(
    *,
    campaign: FactorMiningCampaignSpec,
    selection_record: FactorMiningSelectionRecord,
    validation: FactorCandidateValidation,
    config: FactorPipelineConfig,
    checkpoint_data: tuple[FactorCheckpointData, ...],
    proposals: tuple[FactorPortfolioProposal, ...],
    outcomes: tuple[FactorForwardOutcome, ...],
    run_manifest_hash: str,
    lookahead_certificate_hash: str,
) -> FactorMiningOOSReleaseResult:
    """Build OOS evidence only after the caller has frozen a selection record."""

    _require_validated_candidate(campaign=campaign, validation=validation, config=config)
    validate_global_discovery_oos_layout(campaign)
    factor_definition = validation.factor_definition
    if factor_definition is None:  # pragma: no cover - _require_validated_candidate guards this.
        raise FactorMiningEvaluationError("validated candidate lost its factor definition")
    if selection_record.candidate_id != validation.candidate.candidate_id or (
        selection_record.candidate_hash != validation.candidate.candidate_hash
    ):
        raise FactorMiningEvaluationError("selection record does not bind the evaluated candidate")
    stages = tuple(
        _build_stage_evidence(
            campaign=campaign,
            validation=validation,
            config=config,
            checkpoint_data=checkpoint_data,
            proposals=proposals,
            outcomes=outcomes,
            fold_id=fold.fold_id,
            fold_hash=fold.fold_hash,
            stage=ValidationStage.OUT_OF_SAMPLE,
            period=fold.split.out_of_sample,
            selection_bound=False,
        )
        for fold in campaign.template.walk_forward_folds
    )
    return FactorMiningOOSReleaseResult(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate_id=validation.candidate.candidate_id,
        candidate_hash=validation.candidate.candidate_hash,
        selection_record_hash=selection_record.record_hash,
        factor_definition_hash=factor_definition.definition_hash,
        pipeline_config_hash=config.config_hash,
        run_manifest_hash=run_manifest_hash,
        lookahead_certificate_hash=lookahead_certificate_hash,
        stage_evidence=stages,
    )


def _require_validated_candidate(
    *,
    campaign: FactorMiningCampaignSpec,
    validation: FactorCandidateValidation,
    config: FactorPipelineConfig,
) -> None:
    if type(campaign) is not FactorMiningCampaignSpec:
        raise FactorMiningEvaluationError("campaign must be an exact FactorMiningCampaignSpec")
    if type(validation) is not FactorCandidateValidation:
        raise FactorMiningEvaluationError("validation must be an exact FactorCandidateValidation")
    if type(config) is not FactorPipelineConfig:
        raise FactorMiningEvaluationError("config must be an exact FactorPipelineConfig")
    if validation.status is not CandidateValidationStatus.VALIDATED_FOR_RESEARCH:
        raise FactorMiningEvaluationError("stage evaluation requires a validated research candidate")
    if validation.factor_definition is None:
        raise FactorMiningEvaluationError("validated candidate lost its factor definition")
    if len(config.alpha_factors) != 1 or config.alpha_factors[0] != validation.factor_definition:
        raise FactorMiningEvaluationError("factor-mining stage evaluation requires the sole validated alpha")


def _build_stage_evidence(
    *,
    campaign: FactorMiningCampaignSpec,
    validation: FactorCandidateValidation,
    config: FactorPipelineConfig,
    checkpoint_data: tuple[FactorCheckpointData, ...],
    proposals: tuple[FactorPortfolioProposal, ...],
    outcomes: tuple[FactorForwardOutcome, ...],
    fold_id: str,
    fold_hash: str,
    stage: ValidationStage,
    period: ValidationPeriod,
    selection_bound: bool,
) -> FactorMiningStageEvidence:
    if validation.factor_definition is None:  # pragma: no cover - guarded by caller.
        raise FactorMiningEvaluationError("validated candidate lost its factor definition")
    stage_checkpoints = tuple(
        item for item in checkpoint_data if period.contains(item.decision_session)
    )
    stage_proposals = tuple(
        item for item in proposals if period.contains(item.decision_session)
    )
    if len(stage_checkpoints) != len(stage_proposals) or not stage_checkpoints:
        raise FactorMiningEvaluationError(f"{stage.value} stage lacks checkpoint/proposal coverage")
    market = build_factor_market_frame(stage_checkpoints)
    targets = build_factor_target_frame(stage_proposals, stage_checkpoints)
    origin_outcomes = tuple(item for item in outcomes if period.contains(item.decision_session))
    retained_outcomes = tuple(
        item
        for item in origin_outcomes
        if period.contains(item.evaluation_session)
        and (not selection_bound or item.evaluation_at < campaign.selection_at)
    )
    within_period_after_selection = tuple(
        item
        for item in origin_outcomes
        if period.contains(item.evaluation_session) and item.evaluation_at >= campaign.selection_at
    )
    if selection_bound and within_period_after_selection:
        raise FactorMiningEvaluationError(
            "selection_at must be strictly after every retained discovery outcome evaluation"
        )
    exposures = tuple(
        exposure
        for item in stage_checkpoints
        for exposure in item.exposures
        if exposure.factor_id == validation.factor_definition.factor_id
    )
    analysis = analyze_factor(
        factor_id=validation.factor_definition.factor_id,
        exposures=exposures,
        outcomes=retained_outcomes,
        quantile_count=config.quantile_count,
        min_cross_section=config.min_cross_section,
    )
    cost_results = tuple(
        sorted(
            (
                _run_cost_scenario(
                    market=market,
                    targets=targets,
                    config=config,
                    scenario=scenario,
                )
                for scenario in campaign.selection_policy.cost_scenarios
            ),
            key=lambda item: item.cost_scenario_hash,
        )
    )
    # ``analyze_factor`` records the raw feature relationship.  Discovery
    # policy evaluates the sealed FactorDefinition, whose direction is part of
    # the candidate identity.  Normalising the directional metrics here lets
    # a valid contrarian candidate be selected using the same "positive is
    # favorable" thresholds and sign test as a momentum candidate, without
    # mutating or misrepresenting the underlying raw analysis artifact.
    direction = validation.factor_definition.direction
    return FactorMiningStageEvidence(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate_id=validation.candidate.candidate_id,
        candidate_hash=validation.candidate.candidate_hash,
        factor_definition_hash=validation.factor_definition.definition_hash,
        pipeline_config_hash=config.config_hash,
        fold_id=fold_id,
        fold_hash=fold_hash,
        stage=stage,
        period_start=period.start.isoformat(),
        period_end=period.end.isoformat(),
        analysis_hash=analysis.analysis_hash,
        analysis_period_hashes=tuple(item.period_hash for item in analysis.periods),
        outcome_hashes=tuple(item.outcome_hash for item in retained_outcomes),
        mean_rank_ic=direction * analysis.mean_rank_ic,
        quantile_spread=direction * analysis.quantile_spread,
        mean_factor_turnover=analysis.mean_turnover,
        positive_rank_ic_count=sum(
            1 for item in analysis.periods if direction * item.rank_ic > 0.0
        ),
        purged_cross_boundary_outcome_count=len(origin_outcomes) - len(retained_outcomes),
        cost_results=cost_results,
        stage_boundary_mode=campaign.selection_policy.stage_boundary_mode,
    )


def _run_cost_scenario(
    *,
    market: pl.DataFrame,
    targets: pl.DataFrame,
    config: FactorPipelineConfig,
    scenario,
) -> FactorDiscoveryStageCostResult:
    result = run_event_backtest(
        market,
        targets,
        initial_cash=config.initial_cash,
        commission_bps=scenario.commission_bps,
        min_commission=scenario.min_commission,
        slippage_bps=scenario.slippage_bps,
        execution_delay_sessions=scenario.execution_delay_sessions,
        terminal_flatten=True,
    )
    return FactorDiscoveryStageCostResult(
        cost_scenario_hash=scenario.scenario_hash,
        backtest_result_hash=result.result_hash,
        session_count=len(result.equity_curve),
        total_return=result.total_return,
        max_drawdown=result.max_drawdown,
        portfolio_turnover=result.turnover_estimate,
    )
