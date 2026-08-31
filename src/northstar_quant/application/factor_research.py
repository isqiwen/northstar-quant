"""P11 因子研究的唯一跨域 composition root。

它从 immutable DatasetVersion 的逐 checkpoint PIT 重放开始，产出 research-only 因子组合
提案和连续序列回测。该模块不导入 execution，不激活策略，也不生成 PortfolioTarget 或订单。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import polars as pl

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.research.backtest.event_engine import run_event_backtest
from northstar_quant.research.backtest.models import BacktestResult
from northstar_quant.research.factors.analysis import analyze_factor
from northstar_quant.research.factors.engine import (
    FactorCheckpointComputation,
    FactorEngine,
)
from northstar_quant.research.factors.frames import (
    build_factor_market_frame,
    build_factor_target_frame,
)
from northstar_quant.research.factors.models import (
    FactorAnalysisResult,
    FactorCheckpointData,
    FactorForwardOutcome,
    FactorPipelineConfig,
    FactorPortfolioProposal,
    FactorResearchError,
    FactorResearchExperiment,
    FactorResearchRunManifest,
    FactorRobustnessParameterVariant,
    FactorRobustnessResult,
    FactorWalkForwardResult,
)
from northstar_quant.research.factors.portfolio import build_factor_portfolio_proposal
from northstar_quant.research.factors.robustness import (
    FactorRobustnessEvaluator,
    FactorRobustnessParameterVariantEvidence,
)
from northstar_quant.research.features.catalog import register_canonical_feature
from northstar_quant.research.features.models import FeatureVersion
from northstar_quant.research.features.registry import FeatureRegistry
from northstar_quant.research.validation.lookahead import (
    DecisionMarketDataEvidence,
    DecisionReplayEvidence,
    DecisionReplayPlan,
    FeatureAvailabilityEvidence,
    LookaheadCertificate,
    LookaheadGuard,
    LookaheadInputKind,
    LookaheadInputUsage,
    LookaheadInputUsageDeclaration,
    TargetDecisionEvidence,
)


@dataclass(frozen=True, slots=True)
class FactorResearchRun:
    """一次完整、不可自动升级为候选或交易的因子研究运行。"""

    config: FactorPipelineConfig
    experiment: FactorResearchExperiment
    checkpoint_data: tuple[FactorCheckpointData, ...]
    proposals: tuple[FactorPortfolioProposal, ...]
    outcomes: tuple[FactorForwardOutcome, ...]
    analyses: tuple[FactorAnalysisResult, ...]
    robustness: FactorRobustnessResult
    backtest: BacktestResult
    walk_forward: tuple[FactorWalkForwardResult, ...]
    lookahead_certificate: LookaheadCertificate
    manifest: FactorResearchRunManifest

    def __post_init__(self) -> None:
        """Reject any attempt to combine evidence from different frozen replays."""

        if type(self.config) is not FactorPipelineConfig:
            raise FactorResearchError("factor research run config 必须是精确的 FactorPipelineConfig")
        if type(self.experiment) is not FactorResearchExperiment:
            raise FactorResearchError(
                "factor research run experiment 必须是精确的 FactorResearchExperiment"
            )
        if type(self.robustness) is not FactorRobustnessResult:
            raise FactorResearchError(
                "factor research run robustness 必须是精确的 FactorRobustnessResult"
            )
        if type(self.manifest) is not FactorResearchRunManifest:
            raise FactorResearchError(
                "factor research run manifest 必须是精确的 FactorResearchRunManifest"
            )
        if type(self.backtest) is not BacktestResult:
            raise FactorResearchError("factor research run backtest 必须是精确的 BacktestResult")
        if type(self.lookahead_certificate) is not LookaheadCertificate:
            raise FactorResearchError(
                "factor research run lookahead_certificate 必须是精确的 LookaheadCertificate"
            )
        checkpoints = tuple(self.checkpoint_data)
        proposals = tuple(self.proposals)
        outcomes = tuple(self.outcomes)
        analyses = tuple(self.analyses)
        walk_forward = tuple(self.walk_forward)
        if not checkpoints or not all(type(item) is FactorCheckpointData for item in checkpoints):
            raise FactorResearchError("factor research run checkpoint_data 类型无效")
        if len(proposals) != len(checkpoints) or not all(
            type(item) is FactorPortfolioProposal for item in proposals
        ):
            raise FactorResearchError("factor research run proposals 必须与 checkpoint_data 一一对应")
        if not outcomes or not all(type(item) is FactorForwardOutcome for item in outcomes):
            raise FactorResearchError("factor research run outcomes 类型无效")
        if not analyses or not all(type(item) is FactorAnalysisResult for item in analyses):
            raise FactorResearchError("factor research run analyses 类型无效")
        if not walk_forward or not all(type(item) is FactorWalkForwardResult for item in walk_forward):
            raise FactorResearchError("factor research run walk_forward 类型无效")
        if (
            self.experiment.config_hash != self.config.config_hash
            or self.experiment.code_revision != self.config.code_revision
            or self.robustness.config_hash != self.config.config_hash
            or self.robustness.plan_hash != self.config.robustness_plan.plan_hash
            or self.robustness.experiment.experiment_hash != self.experiment.experiment_hash
        ):
            raise FactorResearchError(
                "factor research run config/experiment/robustness 身份必须精确一致"
            )
        checkpoint_hashes = tuple(item.checkpoint_data_hash for item in checkpoints)
        proposal_hashes = tuple(item.proposal_hash for item in proposals)
        outcome_hashes = tuple(item.outcome_hash for item in outcomes)
        if (
            self.robustness.checkpoint_data_hashes != checkpoint_hashes
            or self.robustness.proposal_hashes != proposal_hashes
            or self.robustness.outcome_hashes != outcome_hashes
        ):
            raise FactorResearchError(
                "factor research run robustness 必须精确绑定本次 replay 的输入/输出证据"
            )
        certificate = self.lookahead_certificate
        if certificate.plan.schedule_hash != self.experiment.decision_replay_plan_hash:
            raise FactorResearchError(
                "factor research run lookahead certificate 必须绑定本次 decision replay plan"
            )
        if (
            len(certificate.plan.checkpoints) != len(checkpoints)
            or len(certificate.reports) != len(checkpoints)
        ):
            raise FactorResearchError(
                "factor research run lookahead certificate 必须精确覆盖本次 checkpoints"
            )
        for data, proposal, replay_checkpoint, report in zip(
            checkpoints,
            proposals,
            certificate.plan.checkpoints,
            certificate.reports,
            strict=True,
        ):
            market_evidence = report.evidence.market_data
            target_evidence = report.evidence.target
            if (
                replay_checkpoint.checkpoint_hash != data.checkpoint_hash
                or replay_checkpoint.decision_at != data.decision_at
                or replay_checkpoint.decision_event_time != data.decision_session
                or replay_checkpoint.dataset_version_hash != data.dataset_version_hash
                or market_evidence.checkpoint != replay_checkpoint
                or market_evidence.evidence_hash != data.market_evidence_hash
                or market_evidence.market_snapshot.snapshot_id != data.snapshot_id
                or target_evidence.decision_at != data.decision_at
                or target_evidence.available_at != data.decision_at
                or target_evidence.source_snapshot_hash != data.snapshot_id
                or target_evidence.target_hash != proposal.proposal_hash
            ):
                raise FactorResearchError(
                    "factor research run lookahead certificate 必须精确绑定本次 checkpoint 与 proposal"
                )
        manifest = self.manifest
        expected_dataset_hashes = tuple(
            sorted({item.dataset_version_hash for item in checkpoints})
        )
        if (
            manifest.config_hash != self.config.config_hash
            or manifest.feature_version_hashes != self.experiment.feature_version_hashes
            or manifest.code_revision != self.config.code_revision
            or manifest.decision_replay_plan_hash
            != self.experiment.decision_replay_plan_hash
            or manifest.experiment_hash != self.experiment.experiment_hash
            or manifest.dataset_version_hashes != expected_dataset_hashes
            or manifest.checkpoint_data_hashes != tuple(sorted(checkpoint_hashes))
            or manifest.proposal_hashes != tuple(sorted(proposal_hashes))
            or manifest.analysis_hashes
            != tuple(sorted(item.analysis_hash for item in analyses))
            or manifest.robustness_plan_hash != self.robustness.plan_hash
            or manifest.robustness_result_hash != self.robustness.result_hash
            or manifest.backtest_result_hash != self.backtest.result_hash
            or manifest.walk_forward_result_hashes
            != tuple(sorted(item.result_hash for item in walk_forward))
            or manifest.lookahead_certificate_hash
            != self.lookahead_certificate.certificate_hash
        ):
            raise FactorResearchError(
                "factor research run manifest 必须精确绑定本次 robustness 与全部 replay evidence"
            )
        object.__setattr__(self, "checkpoint_data", checkpoints)
        object.__setattr__(self, "proposals", proposals)
        object.__setattr__(self, "outcomes", outcomes)
        object.__setattr__(self, "analyses", analyses)
        object.__setattr__(self, "walk_forward", walk_forward)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class FactorResearchDiscoveryReplay:
    """PIT replay truncated at a precommitted discovery cutoff.

    It is intentionally not a full backtest run: it has no walk-forward or
    OOS result.  The factor-mining composition uses it to compute development
    stage evidence without materializing holdout checkpoints.
    """

    config: FactorPipelineConfig
    experiment: FactorResearchExperiment
    checkpoint_data: tuple[FactorCheckpointData, ...]
    proposals: tuple[FactorPortfolioProposal, ...]
    outcomes: tuple[FactorForwardOutcome, ...]
    lookahead_certificate: LookaheadCertificate
    replay_hash: str

    @property
    def research_only(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class _FactorResearchReplayExecution:
    """Private raw replay evidence retained only for robustness verification."""

    replay: FactorResearchDiscoveryReplay
    market_evidences: tuple[DecisionMarketDataEvidence, ...]
    computations: tuple[FactorCheckpointComputation, ...]


@dataclass(frozen=True, slots=True)
class _FactorResearchCoreRun:
    """Internal core replay result, intentionally before robustness labeling."""

    config: FactorPipelineConfig
    experiment: FactorResearchExperiment
    market_evidences: tuple[DecisionMarketDataEvidence, ...]
    computations: tuple[FactorCheckpointComputation, ...]
    checkpoint_data: tuple[FactorCheckpointData, ...]
    proposals: tuple[FactorPortfolioProposal, ...]
    outcomes: tuple[FactorForwardOutcome, ...]
    analyses: tuple[FactorAnalysisResult, ...]
    backtest: BacktestResult
    walk_forward: tuple[FactorWalkForwardResult, ...]
    lookahead_certificate: LookaheadCertificate


class FactorResearchPipeline:
    """受控的 PIT 因子研究流水线，不提供 DataFrame 或 current-time fallback。"""

    def __init__(self, *, artifact_store: ArtifactStore, config: FactorPipelineConfig) -> None:
        if type(artifact_store) is not ArtifactStore:
            raise FactorResearchError("artifact_store 必须是精确的 ArtifactStore")
        if not isinstance(config, FactorPipelineConfig):
            raise FactorResearchError("config 必须是 FactorPipelineConfig")
        self._artifact_store = artifact_store
        self._config = config

    def run(self, *, plan: DecisionReplayPlan) -> FactorResearchRun:
        """执行严格 PIT replay、因子研究、连续收益回测和固定 OOS fold 验证。"""

        core = self._run_core(plan)
        variant_evidence = tuple(
            self._parameter_variant_evidence(plan=plan, variant=variant)
            for variant in self._config.robustness_plan.parameter_variants
        )
        robustness = FactorRobustnessEvaluator(artifact_store=self._artifact_store).evaluate(
            config=self._config,
            experiment=core.experiment,
            decision_replay_plan=plan,
            market_evidences=core.market_evidences,
            computations=core.computations,
            checkpoint_data=core.checkpoint_data,
            proposals=core.proposals,
            outcomes=core.outcomes,
            parameter_variant_evidence=variant_evidence,
        )
        manifest = FactorResearchRunManifest(
            config_hash=self._config.config_hash,
            feature_version_hashes=core.experiment.feature_version_hashes,
            code_revision=self._config.code_revision,
            decision_replay_plan_hash=plan.schedule_hash,
            experiment_hash=core.experiment.experiment_hash,
            dataset_version_hashes=tuple(
                sorted({item.dataset_version_hash for item in core.checkpoint_data})
            ),
            checkpoint_data_hashes=tuple(
                item.checkpoint_data_hash for item in core.checkpoint_data
            ),
            proposal_hashes=tuple(item.proposal_hash for item in core.proposals),
            analysis_hashes=tuple(item.analysis_hash for item in core.analyses),
            robustness_plan_hash=self._config.robustness_plan.plan_hash,
            robustness_result_hash=robustness.result_hash,
            backtest_result_hash=core.backtest.result_hash,
            walk_forward_result_hashes=tuple(item.result_hash for item in core.walk_forward),
            lookahead_certificate_hash=core.lookahead_certificate.certificate_hash,
        )
        return FactorResearchRun(
            config=self._config,
            experiment=core.experiment,
            checkpoint_data=core.checkpoint_data,
            proposals=core.proposals,
            outcomes=core.outcomes,
            analyses=core.analyses,
            robustness=robustness,
            backtest=core.backtest,
            walk_forward=core.walk_forward,
            lookahead_certificate=core.lookahead_certificate,
            manifest=manifest,
        )

    def _run_core(self, plan: DecisionReplayPlan) -> _FactorResearchCoreRun:
        """Run one exact configuration without recursively invoking robustness."""

        execution = self._replay_execution(plan)
        replay = execution.replay
        market_frame = build_factor_market_frame(replay.checkpoint_data)
        target_frame = build_factor_target_frame(replay.proposals, replay.checkpoint_data)
        backtest = run_event_backtest(
            market_frame,
            target_frame,
            initial_cash=self._config.initial_cash,
            commission_bps=self._config.commission_bps,
            min_commission=self._config.min_commission,
            slippage_bps=self._config.slippage_bps,
            execution_delay_sessions=self._config.execution_delay_sessions,
        )
        analyses = tuple(
            analyze_factor(
                factor_id=definition.factor_id,
                exposures=tuple(
                    exposure for item in replay.checkpoint_data for exposure in item.exposures
                ),
                outcomes=replay.outcomes,
                quantile_count=self._config.quantile_count,
                min_cross_section=self._config.min_cross_section,
            )
            for definition in self._config.alpha_factors
        )
        walk_forward = _walk_forward_results(
            config=self._config,
            market_frame=market_frame,
            target_frame=target_frame,
        )
        return _FactorResearchCoreRun(
            config=self._config,
            experiment=replay.experiment,
            market_evidences=execution.market_evidences,
            computations=execution.computations,
            checkpoint_data=replay.checkpoint_data,
            proposals=replay.proposals,
            outcomes=replay.outcomes,
            analyses=analyses,
            backtest=backtest,
            walk_forward=walk_forward,
            lookahead_certificate=replay.lookahead_certificate,
        )

    def _parameter_variant_evidence(
        self,
        *,
        plan: DecisionReplayPlan,
        variant: FactorRobustnessParameterVariant,
    ) -> FactorRobustnessParameterVariantEvidence:
        variant_config = self._config.with_parameter_variant(variant)
        variant_core = FactorResearchPipeline(
            artifact_store=self._artifact_store,
            config=variant_config,
        )._run_core(plan)
        analysis = next(
            (item for item in variant_core.analyses if item.factor_id == variant.factor_id),
            None,
        )
        if analysis is None:  # Defensive: FactorPipelineConfig validated alpha factor identity.
            raise FactorResearchError("robustness 参数邻域重跑缺少对应 alpha analysis")
        return FactorRobustnessParameterVariantEvidence(
            variant=variant,
            experiment=variant_core.experiment,
            market_evidences=variant_core.market_evidences,
            computations=variant_core.computations,
            checkpoint_data=variant_core.checkpoint_data,
            proposals=variant_core.proposals,
            outcomes=variant_core.outcomes,
            analysis=analysis,
        )

    def run_discovery(
        self,
        *,
        plan: DecisionReplayPlan,
        selection_at: datetime,
    ) -> FactorResearchDiscoveryReplay:
        """Replay only checkpoint data known by a sealed discovery selection time."""

        if type(plan) is not DecisionReplayPlan:
            raise FactorResearchError("plan 必须是精确的 DecisionReplayPlan")
        if not isinstance(selection_at, datetime) or selection_at.tzinfo is None:
            raise FactorResearchError("selection_at 必须是带时区 datetime")
        selected = tuple(
            checkpoint for checkpoint in plan.checkpoints if checkpoint.decision_at <= selection_at
        )
        if not selected:
            raise FactorResearchError("selection_at 之前没有可重放的 factor checkpoint")
        return self._replay(DecisionReplayPlan.create(selected))

    def _replay(self, plan: DecisionReplayPlan) -> FactorResearchDiscoveryReplay:
        """Materialize only strict PIT inputs, features, proposals, and matured outcomes."""

        return self._replay_execution(plan).replay

    def _replay_execution(self, plan: DecisionReplayPlan) -> _FactorResearchReplayExecution:
        """Retain raw trusted replay artifacts for downstream provenance checks."""

        if type(plan) is not DecisionReplayPlan:
            raise FactorResearchError("plan 必须是精确的 DecisionReplayPlan")
        registry = FeatureRegistry(artifact_store=self._artifact_store)
        feature_versions = self._register_feature_versions(registry)
        experiment = FactorResearchExperiment(
            experiment_id=self._config.pipeline_id,
            config_hash=self._config.config_hash,
            decision_replay_plan_hash=plan.schedule_hash,
            dataset_version_hashes=tuple(
                sorted({checkpoint.dataset_version_hash for checkpoint in plan.checkpoints})
            ),
            feature_version_hashes=tuple(
                sorted({item.version_hash for item in feature_versions.values()})
            ),
            code_revision=self._config.code_revision,
        )
        engine = FactorEngine(
            config=self._config,
            registry=registry,
            feature_versions=feature_versions,
        )
        market_evidences = DecisionReplayPlan.replay_market_data(plan, self._artifact_store)
        computations = tuple(engine.compute(evidence) for evidence in market_evidences)
        checkpoint_data = tuple(item.data for item in computations)
        proposals = tuple(
            build_factor_portfolio_proposal(config=self._config, checkpoint_data=item)
            for item in checkpoint_data
        )
        certificate = self._certify_lookahead(plan, market_evidences, computations, proposals)
        outcomes = _forward_outcomes(
            checkpoint_data,
            holding_period_sessions=self._config.holding_period_sessions,
        )
        replay_hash = canonical_json_sha256(
            {
                "checkpoint_data_hashes": [item.checkpoint_data_hash for item in checkpoint_data],
                "config_hash": self._config.config_hash,
                "experiment_hash": experiment.experiment_hash,
                "format": "northstar.factor-research-discovery-replay.v1",
                "lookahead_certificate_hash": certificate.certificate_hash,
                "outcome_hashes": [item.outcome_hash for item in outcomes],
                "plan_hash": plan.schedule_hash,
                "proposal_hashes": [item.proposal_hash for item in proposals],
                "research_only": True,
            }
        )
        return _FactorResearchReplayExecution(
            replay=FactorResearchDiscoveryReplay(
                config=self._config,
                experiment=experiment,
                checkpoint_data=checkpoint_data,
                proposals=proposals,
                outcomes=outcomes,
                lookahead_certificate=certificate,
                replay_hash=replay_hash,
            ),
            market_evidences=market_evidences,
            computations=computations,
        )

    def _register_feature_versions(self, registry: FeatureRegistry) -> dict[str, FeatureVersion]:
        versions: dict[str, FeatureVersion] = {}
        for definition in self._config.factors:
            versions[definition.factor_id] = register_canonical_feature(
                registry,
                feature_id=definition.feature_id,
                version=self._config.feature_version,
                code_revision=self._config.code_revision,
            )
        return versions

    def _certify_lookahead(
        self,
        plan: DecisionReplayPlan,
        market_evidences,
        computations: tuple[FactorCheckpointComputation, ...],
        proposals: tuple[FactorPortfolioProposal, ...],
    ) -> LookaheadCertificate:
        if not (
            len(market_evidences) == len(computations) == len(proposals) == len(plan.checkpoints)
        ):
            raise FactorResearchError("PIT checkpoint、factor computation 与 proposal 数量不一致")
        producer_hash = canonical_json_sha256(
            {
                "config_hash": self._config.config_hash,
                "format": "northstar.factor-research-producer.v1",
            }
        )
        usage = (
            LookaheadInputUsageDeclaration(
                input_kind=LookaheadInputKind.FEATURE,
                usage=LookaheadInputUsage.PROVIDED,
                producer_identity_hash=producer_hash,
            ),
            LookaheadInputUsageDeclaration(
                input_kind=LookaheadInputKind.EVENT,
                usage=LookaheadInputUsage.NOT_USED,
                producer_identity_hash=producer_hash,
            ),
            LookaheadInputUsageDeclaration(
                input_kind=LookaheadInputKind.CONTRACT,
                usage=LookaheadInputUsage.NOT_USED,
                producer_identity_hash=producer_hash,
            ),
            LookaheadInputUsageDeclaration(
                input_kind=LookaheadInputKind.FEE_MARGIN_RULE,
                usage=LookaheadInputUsage.NOT_USED,
                producer_identity_hash=producer_hash,
            ),
        )
        evidence: list[DecisionReplayEvidence] = []
        for market, computation, proposal in zip(
            market_evidences,
            computations,
            proposals,
            strict=True,
        ):
            if proposal.checkpoint_hash != market.checkpoint.checkpoint_hash:
                raise FactorResearchError("proposal 与 market checkpoint 不一致")
            feature_evidence = tuple(
                FeatureAvailabilityEvidence.from_replay_materialization(materialization)
                for _, materialization in computation.materializations
            )
            evidence.append(
                DecisionReplayEvidence(
                    market_data=market,
                    target=TargetDecisionEvidence(
                        decision_at=market.checkpoint.decision_at,
                        available_at=market.checkpoint.decision_at,
                        source_snapshot_hash=market.market_snapshot.snapshot_id,
                        target_hash=proposal.proposal_hash,
                    ),
                    features=feature_evidence,
                    input_usage=usage,
                    require_execution_rules=False,
                )
            )
        return LookaheadGuard().certify(plan, tuple(evidence), artifact_store=self._artifact_store)


def _forward_outcomes(
    checkpoints: tuple[FactorCheckpointData, ...],
    *,
    holding_period_sessions: int,
) -> tuple[FactorForwardOutcome, ...]:
    if holding_period_sessions < 1:
        raise FactorResearchError("holding_period_sessions 必须大于等于 1")
    outcomes: list[FactorForwardOutcome] = []
    for offset, origin in enumerate(checkpoints):
        evaluation_offset = offset + holding_period_sessions
        if evaluation_offset >= len(checkpoints):
            continue
        evaluation = checkpoints[evaluation_offset]
        origin_prices = {item.symbol: item.close for item in origin.market_slices}
        evaluation_prices = {item.symbol: item.close for item in evaluation.market_slices}
        if set(origin_prices) != set(evaluation_prices):
            raise FactorResearchError("forward outcome 不得在前后 checkpoint 静默缩小标的池")
        for symbol in sorted(origin_prices):
            outcomes.append(
                FactorForwardOutcome(
                    origin_checkpoint_hash=origin.checkpoint_hash,
                    decision_session=origin.decision_session,
                    evaluation_checkpoint_hash=evaluation.checkpoint_hash,
                    evaluation_session=evaluation.decision_session,
                    evaluation_at=evaluation.decision_at,
                    symbol=symbol,
                    forward_return=evaluation_prices[symbol] / origin_prices[symbol] - 1.0,
                )
            )
    if not outcomes:
        raise FactorResearchError("没有任何已到期 forward outcome，无法运行 factor analysis")
    return tuple(outcomes)


def _walk_forward_results(
    *,
    config: FactorPipelineConfig,
    market_frame: pl.DataFrame,
    target_frame: pl.DataFrame,
) -> tuple[FactorWalkForwardResult, ...]:
    results: list[FactorWalkForwardResult] = []
    for fold in config.walk_forward_folds:
        period = fold.split.out_of_sample
        market = market_frame.filter(
            (pl.col("date") >= period.start) & (pl.col("date") <= period.end)
        )
        targets = target_frame.filter(
            (pl.col("date") >= period.start) & (pl.col("date") <= period.end)
        )
        if market.is_empty() or targets.is_empty():
            raise FactorResearchError(f"walk-forward fold {fold.fold_id} 缺少 OOS 行情或 proposal")
        sessions = market.get_column("date").unique().sort().to_list()
        if len(sessions) < 2:
            raise FactorResearchError(f"walk-forward fold {fold.fold_id} 至少需要两个 OOS session")
        result = run_event_backtest(
            market,
            targets,
            initial_cash=config.initial_cash,
            commission_bps=config.commission_bps,
            min_commission=config.min_commission,
            slippage_bps=config.slippage_bps,
            execution_delay_sessions=config.execution_delay_sessions,
        )
        results.append(
            FactorWalkForwardResult(
                fold_id=fold.fold_id,
                fold_hash=fold.fold_hash,
                backtest_result_hash=result.result_hash,
                session_count=len(sessions),
                total_return=result.total_return,
                max_drawdown=result.max_drawdown,
            )
        )
    return tuple(results)


__all__ = ["FactorResearchPipeline", "FactorResearchRun"]
