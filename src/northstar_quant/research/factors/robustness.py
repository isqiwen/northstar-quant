"""Frozen robustness studies for continuous-series factor research.

The evaluator is deliberately downstream of PIT feature computation and factor
portfolio proposal construction.  It can label a completed research run with
precommitted ex-post evidence, but it cannot mutate a feature, proposal, or
selection decision.  All price paths remain continuous research series; this
module never introduces actual-contract execution semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.research.backtest.event_engine import run_event_backtest
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
    FactorExposure,
    FactorForwardOutcome,
    FactorPipelineConfig,
    FactorPortfolioProposal,
    FactorResearchError,
    FactorResearchExperiment,
    FactorRobustnessCostScenarioResult,
    FactorRobustnessFactorSummary,
    FactorRobustnessParameterVariant,
    FactorRobustnessParameterVariantResult,
    FactorRobustnessResult,
    FactorRobustnessScenarioResult,
    FactorStabilityThresholds,
)
from northstar_quant.research.features.catalog import register_canonical_feature
from northstar_quant.research.features.models import FeatureVersion
from northstar_quant.research.features.registry import FeatureRegistry
from northstar_quant.research.validation.lookahead import DecisionReplayPlan
from northstar_quant.research.validation.lookahead import DecisionMarketDataEvidence


@dataclass(frozen=True, slots=True)
class FactorRobustnessParameterVariantEvidence:
    """Evidence produced by a full PIT rerun for one declared neighbour."""

    variant: FactorRobustnessParameterVariant
    experiment: FactorResearchExperiment
    market_evidences: tuple[DecisionMarketDataEvidence, ...]
    computations: tuple[FactorCheckpointComputation, ...]
    checkpoint_data: tuple[FactorCheckpointData, ...]
    proposals: tuple[FactorPortfolioProposal, ...]
    outcomes: tuple[FactorForwardOutcome, ...]
    analysis: FactorAnalysisResult

    def __post_init__(self) -> None:
        if type(self.variant) is not FactorRobustnessParameterVariant:
            raise FactorResearchError(
                "robustness variant evidence 必须引用精确的 FactorRobustnessParameterVariant"
            )
        if type(self.experiment) is not FactorResearchExperiment:
            raise FactorResearchError(
                "robustness variant evidence 必须携带精确的 FactorResearchExperiment"
            )
        if not isinstance(self.market_evidences, tuple) or not self.market_evidences:
            raise FactorResearchError("robustness variant evidence 必须携带非空 market_evidences 元组")
        if not isinstance(self.computations, tuple) or not self.computations:
            raise FactorResearchError("robustness variant evidence 必须携带非空 computations 元组")
        if not isinstance(self.checkpoint_data, tuple) or not self.checkpoint_data:
            raise FactorResearchError("robustness variant evidence 必须携带非空 checkpoint_data 元组")
        if not isinstance(self.proposals, tuple) or not self.proposals:
            raise FactorResearchError("robustness variant evidence 必须携带非空 proposals 元组")
        if not isinstance(self.outcomes, tuple) or not self.outcomes:
            raise FactorResearchError("robustness variant evidence 必须携带非空 outcomes 元组")
        if type(self.analysis) is not FactorAnalysisResult:
            raise FactorResearchError(
                "robustness variant evidence 必须携带精确的 FactorAnalysisResult"
            )


class FactorRobustnessEvaluator:
    """Evaluate exactly the immutable study axes bound into a pipeline config."""

    __slots__ = ("_artifact_store",)

    def __init__(self, *, artifact_store: ArtifactStore) -> None:
        if type(artifact_store) is not ArtifactStore:
            raise FactorResearchError("robustness artifact_store 必须是精确的 ArtifactStore")
        self._artifact_store = artifact_store

    def evaluate(
        self,
        *,
        config: FactorPipelineConfig,
        experiment: FactorResearchExperiment,
        decision_replay_plan: DecisionReplayPlan,
        market_evidences: tuple[DecisionMarketDataEvidence, ...],
        computations: tuple[FactorCheckpointComputation, ...],
        checkpoint_data: tuple[FactorCheckpointData, ...],
        proposals: tuple[FactorPortfolioProposal, ...],
        outcomes: tuple[FactorForwardOutcome, ...],
        parameter_variant_evidence: tuple[FactorRobustnessParameterVariantEvidence, ...],
    ) -> FactorRobustnessResult:
        if type(config) is not FactorPipelineConfig:
            raise FactorResearchError("robustness config 必须是精确的 FactorPipelineConfig")
        if type(experiment) is not FactorResearchExperiment:
            raise FactorResearchError("robustness experiment 必须是精确的 FactorResearchExperiment")
        if type(decision_replay_plan) is not DecisionReplayPlan:
            raise FactorResearchError("robustness decision_replay_plan 必须是精确的 DecisionReplayPlan")
        checkpoints = tuple(checkpoint_data)
        factor_proposals = tuple(proposals)
        matured_outcomes = tuple(outcomes)
        replay_market_evidences = tuple(market_evidences)
        factor_computations = tuple(computations)
        self._validate_core_evidence(
            config=config,
            experiment=experiment,
            decision_replay_plan=decision_replay_plan,
            market_evidences=replay_market_evidences,
            computations=factor_computations,
            checkpoints=checkpoints,
            proposals=factor_proposals,
            outcomes=matured_outcomes,
        )

        plan = config.robustness_plan
        universe = _continuous_symbol_universe(checkpoints)
        all_exposures = tuple(exposure for item in checkpoints for exposure in item.exposures)
        scenario_results = self._evaluate_subperiods(
            config=config,
            exposures=all_exposures,
            outcomes=matured_outcomes,
            universe=universe,
        )
        parameter_results = self._evaluate_parameter_variants(
            config=config,
            base_experiment=experiment,
            base_checkpoints=checkpoints,
            decision_replay_plan=decision_replay_plan,
            evidence=parameter_variant_evidence,
        )
        cost_results = self._evaluate_cost_scenarios(
            config=config,
            checkpoints=checkpoints,
            proposals=factor_proposals,
        )
        factor_summaries = self._factor_summaries(
            config=config,
            scenario_results=scenario_results,
        )
        return FactorRobustnessResult(
            plan=plan,
            config=config,
            experiment=experiment,
            checkpoint_data_hashes=tuple(
                item.checkpoint_data_hash for item in checkpoints
            ),
            proposal_hashes=tuple(item.proposal_hash for item in factor_proposals),
            outcome_hashes=tuple(item.outcome_hash for item in matured_outcomes),
            scenario_results=scenario_results,
            parameter_variant_results=parameter_results,
            cost_scenario_results=cost_results,
            factor_summaries=factor_summaries,
        )

    def _validate_core_evidence(
        self,
        *,
        config: FactorPipelineConfig,
        experiment: FactorResearchExperiment,
        decision_replay_plan: DecisionReplayPlan,
        market_evidences: tuple[DecisionMarketDataEvidence, ...],
        computations: tuple[FactorCheckpointComputation, ...],
        checkpoints: tuple[FactorCheckpointData, ...],
        proposals: tuple[FactorPortfolioProposal, ...],
        outcomes: tuple[FactorForwardOutcome, ...],
        expected_base_experiment: FactorResearchExperiment | None = None,
        expected_base_checkpoints: tuple[FactorCheckpointData, ...] | None = None,
    ) -> None:
        """Fail closed unless every artifact is from this exact frozen PIT replay.

        An analysis hash intentionally only summarizes statistical output.  It
        cannot establish configuration, data-version, or replay provenance on
        its own, so this boundary validates the raw replay objects before any
        robustness statistic is accepted.
        """

        if experiment.config_hash != config.config_hash:
            raise FactorResearchError("robustness experiment 不属于当前 pipeline config")
        if experiment.decision_replay_plan_hash != decision_replay_plan.schedule_hash:
            raise FactorResearchError("robustness experiment 不属于当前 decision replay plan")
        if experiment.code_revision != config.code_revision:
            raise FactorResearchError("robustness experiment code_revision 与 config 不一致")
        if not checkpoints or not all(type(item) is FactorCheckpointData for item in checkpoints):
            raise FactorResearchError("robustness checkpoint_data 必须是非空 FactorCheckpointData 序列")
        if len(checkpoints) != len(decision_replay_plan.checkpoints):
            raise FactorResearchError("robustness checkpoint_data 必须精确覆盖 decision replay plan")
        self._validate_raw_replay_evidence(
            config=config,
            decision_replay_plan=decision_replay_plan,
            market_evidences=market_evidences,
            computations=computations,
            checkpoints=checkpoints,
        )
        definitions = {item.factor_id: item for item in config.factors}
        expected_factor_ids = tuple(definitions)
        feature_version_hashes: set[str] = set()
        dataset_version_hashes: set[str] = set()
        expected_universe: frozenset[str] | None = None
        for data, replay_checkpoint in zip(
            checkpoints,
            decision_replay_plan.checkpoints,
            strict=True,
        ):
            if (
                data.checkpoint_hash != replay_checkpoint.checkpoint_hash
                or data.decision_at != replay_checkpoint.decision_at
                or data.dataset_version_hash != replay_checkpoint.dataset_version_hash
                or data.config_hash != config.config_hash
            ):
                raise FactorResearchError(
                    "robustness checkpoint_data 必须精确绑定 config 与 decision replay checkpoint"
                )
            decision_event_time = replay_checkpoint.decision_event_time
            if data.decision_session != decision_event_time:
                raise FactorResearchError(
                    "robustness checkpoint_data.decision_session 与 replay checkpoint 不一致"
                )
            if tuple(item.factor_id for item in data.materializations) != expected_factor_ids:
                raise FactorResearchError(
                    "robustness checkpoint materializations 必须精确覆盖 config.factors"
                )
            for reference in data.materializations:
                definition = definitions[reference.factor_id]
                if reference.factor_definition_hash != definition.definition_hash:
                    raise FactorResearchError(
                        "robustness checkpoint materialization 与 config factor definition 不一致"
                    )
                feature_version_hashes.add(reference.feature_version_hash)
            if any(
                exposure.factor_id not in definitions
                or exposure.factor_definition_hash
                != definitions[exposure.factor_id].definition_hash
                or exposure.config_hash != config.config_hash
                for exposure in data.exposures
            ):
                raise FactorResearchError(
                    "robustness checkpoint exposure 与 config factor definition 不一致"
                )
            universe = frozenset(item.symbol for item in data.market_slices)
            if not universe:
                raise FactorResearchError("robustness checkpoint 不包含连续研究标的")
            if expected_universe is None:
                expected_universe = universe
            elif universe != expected_universe:
                raise FactorResearchError("robustness 不接受变化的连续研究标的池")
            dataset_version_hashes.add(data.dataset_version_hash)
        if tuple(sorted(dataset_version_hashes)) != experiment.dataset_version_hashes:
            raise FactorResearchError("robustness experiment dataset versions 与 replay 不一致")
        if tuple(sorted(feature_version_hashes)) != experiment.feature_version_hashes:
            raise FactorResearchError("robustness experiment feature versions 与 replay 不一致")
        if expected_base_experiment is not None:
            if (
                experiment.decision_replay_plan_hash
                != expected_base_experiment.decision_replay_plan_hash
                or experiment.dataset_version_hashes
                != expected_base_experiment.dataset_version_hashes
                or experiment.feature_version_hashes
                != expected_base_experiment.feature_version_hashes
                or experiment.code_revision != expected_base_experiment.code_revision
            ):
                raise FactorResearchError(
                    "robustness parameter evidence 必须使用基线 run 的 replay/data/code identity"
                )
        if expected_base_checkpoints is not None:
            if len(checkpoints) != len(expected_base_checkpoints):
                raise FactorResearchError(
                    "robustness parameter evidence checkpoint 数量必须与基线 run 一致"
                )
            for variant, base in zip(checkpoints, expected_base_checkpoints, strict=True):
                if (
                    variant.checkpoint_hash != base.checkpoint_hash
                    or variant.decision_at != base.decision_at
                    or variant.decision_session != base.decision_session
                    or variant.market_evidence_hash != base.market_evidence_hash
                    or variant.snapshot_id != base.snapshot_id
                    or variant.dataset_version_hash != base.dataset_version_hash
                    or variant.market_slices != base.market_slices
                ):
                    raise FactorResearchError(
                        "robustness parameter evidence 必须重放基线 run 的同一 PIT market evidence"
                    )
        if len(proposals) != len(checkpoints) or not all(
            type(item) is FactorPortfolioProposal for item in proposals
        ):
            raise FactorResearchError("robustness proposals 必须与 checkpoint_data 一一对应")
        for proposal, data in zip(proposals, checkpoints, strict=True):
            if (
                proposal.checkpoint_hash != data.checkpoint_hash
                or proposal.decision_at != data.decision_at
                or proposal.decision_session != data.decision_session
                or proposal.snapshot_id != data.snapshot_id
                or proposal.checkpoint_data_hash != data.checkpoint_data_hash
                or proposal.config_hash != config.config_hash
            ):
                raise FactorResearchError(
                    "robustness proposal 必须精确绑定当前 config/checkpoint data"
                )
            symbols = {item.symbol for item in data.market_slices}
            if any(item.symbol not in symbols for item in proposal.weights):
                raise FactorResearchError("robustness proposal 不能引用 checkpoint 外的连续研究标的")
        if not outcomes or not all(type(item) is FactorForwardOutcome for item in outcomes):
            raise FactorResearchError("robustness outcomes 必须是非空、已到期 FactorForwardOutcome 序列")
        expected_outcomes: list[FactorForwardOutcome] = []
        for index, origin in enumerate(checkpoints):
            evaluation_index = index + config.holding_period_sessions
            if evaluation_index >= len(checkpoints):
                continue
            evaluation = checkpoints[evaluation_index]
            origin_prices = {item.symbol: item.close for item in origin.market_slices}
            evaluation_prices = {item.symbol: item.close for item in evaluation.market_slices}
            if set(origin_prices) != set(evaluation_prices):
                raise FactorResearchError(
                    "robustness forward outcome 不得在前后 checkpoint 缩小连续研究标的池"
                )
            expected_outcomes.extend(
                FactorForwardOutcome(
                    origin_checkpoint_hash=origin.checkpoint_hash,
                    decision_session=origin.decision_session,
                    evaluation_checkpoint_hash=evaluation.checkpoint_hash,
                    evaluation_session=evaluation.decision_session,
                    evaluation_at=evaluation.decision_at,
                    symbol=symbol,
                    forward_return=evaluation_prices[symbol] / origin_prices[symbol] - 1.0,
                )
                for symbol in sorted(origin_prices)
            )
        if outcomes != tuple(expected_outcomes):
            raise FactorResearchError(
                "robustness outcomes 必须精确由当前 PIT checkpoints 的 holding period 推导"
            )

    def _validate_raw_replay_evidence(
        self,
        *,
        config: FactorPipelineConfig,
        decision_replay_plan: DecisionReplayPlan,
        market_evidences: tuple[DecisionMarketDataEvidence, ...],
        computations: tuple[FactorCheckpointComputation, ...],
        checkpoints: tuple[FactorCheckpointData, ...],
    ) -> None:
        """Re-select and cross-check the source PIT evidence and feature outputs.

        ``FactorCheckpointData`` is an intentionally compact research object;
        its hashes alone cannot prove which immutable market selection and
        controlled feature materialization produced it.  This verifier retains
        the raw replay evidence at the application seam, independently
        re-selects it from the configured immutable store, and then checks the
        exact checkpoint market slices and exposures derived from the
        Registry-issued materializations.
        """

        if len(market_evidences) != len(checkpoints) or not all(
            type(item) is DecisionMarketDataEvidence for item in market_evidences
        ):
            raise FactorResearchError(
                "robustness market_evidences 必须与 checkpoint_data 一一对应"
            )
        if len(computations) != len(checkpoints) or not all(
            type(item) is FactorCheckpointComputation for item in computations
        ):
            raise FactorResearchError(
                "robustness computations 必须与 checkpoint_data 一一对应"
            )
        reselected_evidences = decision_replay_plan.replay_market_data(self._artifact_store)
        definitions = {item.factor_id: item for item in config.factors}
        expected_factor_ids = tuple(definitions)
        canonical_registry = FeatureRegistry(artifact_store=self._artifact_store)
        canonical_feature_versions: dict[str, FeatureVersion] = {}
        for factor_id, definition in definitions.items():
            canonical_feature_versions[factor_id] = register_canonical_feature(
                canonical_registry,
                feature_id=definition.feature_id,
                version=config.feature_version,
                code_revision=config.code_revision,
            )
        for data, supplied_evidence, reselected_evidence, computation in zip(
            checkpoints,
            market_evidences,
            reselected_evidences,
            computations,
            strict=True,
        ):
            if (
                supplied_evidence.checkpoint.checkpoint_hash
                != reselected_evidence.checkpoint.checkpoint_hash
                or supplied_evidence.evidence_hash != reselected_evidence.evidence_hash
                or supplied_evidence.market_snapshot.snapshot_id
                != reselected_evidence.market_snapshot.snapshot_id
            ):
                raise FactorResearchError(
                    "robustness market evidence 必须由 immutable store 对当前 replay plan 重选"
                )
            if (
                data.market_evidence_hash != reselected_evidence.evidence_hash
                or data.snapshot_id != reselected_evidence.market_snapshot.snapshot_id
            ):
                raise FactorResearchError(
                    "robustness checkpoint_data 必须精确绑定重选后的 PIT market evidence"
                )
            if computation.data != data:
                raise FactorResearchError(
                    "robustness computation.data 必须精确匹配 checkpoint_data"
                )
            if tuple(item[0] for item in computation.materializations) != expected_factor_ids:
                raise FactorResearchError(
                    "robustness computations 必须精确覆盖 config.factors"
                )
            references = {item.factor_id: item for item in data.materializations}
            expected_exposures: list[FactorExposure] = []
            for factor_id, materialization in computation.materializations:
                definition = definitions[factor_id]
                reference = references[factor_id]
                expected_materialization = (
                    canonical_registry.materialize_per_decision_replay(
                        feature_version_hash=canonical_feature_versions[factor_id].version_hash,
                        market_snapshot=reselected_evidence.market_snapshot,
                        replay_checkpoint_hash=data.checkpoint_hash,
                        parameters=definition.parameters,
                    )
                )
                if materialization != expected_materialization:
                    raise FactorResearchError(
                        "robustness feature materialization 必须由当前 canonical feature、"
                        "参数与 PIT snapshot 精确重放"
                    )
                if (
                    materialization.replay_checkpoint_hash != data.checkpoint_hash
                    or materialization.input_snapshot_hash
                    != reselected_evidence.market_snapshot.snapshot_id
                    or materialization.lineage.feature_version_hash
                    != canonical_feature_versions[factor_id].version_hash
                    or materialization.lineage.feature_version_hash
                    != reference.feature_version_hash
                    or materialization.lineage.decision_at != data.decision_at
                    or materialization.lineage.available_at != data.decision_at
                    or reference.factor_definition_hash != definition.definition_hash
                    or reference.materialization_hash != materialization.materialization_hash
                ):
                    raise FactorResearchError(
                        "robustness feature materialization 必须精确绑定 config 与 PIT snapshot"
                    )
                for value in materialization.values:
                    if value.event_time != data.decision_session or value.value is None:
                        continue
                    raw_symbol = value.key.get("symbol")
                    if not isinstance(raw_symbol, str):
                        raise FactorResearchError(
                            "robustness feature materialization value 必须包含 symbol key"
                        )
                    expected_exposures.append(
                        FactorExposure(
                            checkpoint_hash=data.checkpoint_hash,
                            decision_at=data.decision_at,
                            decision_session=data.decision_session,
                            snapshot_id=data.snapshot_id,
                            factor_id=factor_id,
                            factor_definition_hash=definition.definition_hash,
                            config_hash=config.config_hash,
                            materialization_hash=materialization.materialization_hash,
                            symbol=raw_symbol,
                            value=value.value,
                        )
                    )
            if data.exposures != tuple(
                sorted(expected_exposures, key=lambda item: (item.factor_id, item.symbol))
            ):
                raise FactorResearchError(
                    "robustness checkpoint exposures 必须精确来自受控 feature materializations"
                )
            expected_market_slices = FactorEngine._market_slices(
                reselected_evidence,
                data.decision_session,
            )
            if data.market_slices != expected_market_slices:
                raise FactorResearchError(
                    "robustness checkpoint market slices 必须精确来自重选后的 PIT snapshot"
                )

    def _evaluate_subperiods(
        self,
        *,
        config: FactorPipelineConfig,
        exposures: tuple[FactorExposure, ...],
        outcomes: tuple[FactorForwardOutcome, ...],
        universe: frozenset[str],
    ) -> tuple[FactorRobustnessScenarioResult, ...]:
        thresholds = config.robustness_plan.stability_thresholds
        results: list[FactorRobustnessScenarioResult] = []
        for scenario in config.robustness_plan.subperiods:
            unknown_symbols = sorted(set(scenario.excluded_symbols).difference(universe))
            if unknown_symbols:
                raise FactorResearchError(
                    "robustness symbol exclusion 引用了不在连续研究品种池中的标的："
                    + ", ".join(unknown_symbols)
                )
            if len(universe.difference(scenario.excluded_symbols)) < config.min_cross_section:
                raise FactorResearchError(
                    f"robustness scenario {scenario.scenario_id} 剔除后不满足最小横截面"
                )
            selected_exposures = tuple(
                item
                for item in exposures
                if scenario.period.start <= item.decision_session <= scenario.period.end
                and item.symbol not in scenario.excluded_symbols
            )
            selected_outcomes = tuple(
                item
                for item in outcomes
                if scenario.period.start <= item.decision_session <= scenario.period.end
                and scenario.period.start <= item.evaluation_session <= scenario.period.end
                and item.symbol not in scenario.excluded_symbols
            )
            for definition in config.alpha_factors:
                analysis = analyze_factor(
                    factor_id=definition.factor_id,
                    exposures=selected_exposures,
                    outcomes=selected_outcomes,
                    quantile_count=config.quantile_count,
                    min_cross_section=config.min_cross_section,
                )
                results.append(
                    _scenario_result(
                        scenario_id=scenario.scenario_id,
                        analysis=analysis,
                        thresholds=thresholds,
                    )
                )
        return tuple(sorted(results, key=lambda item: (item.scenario_id, item.factor_id)))

    def _evaluate_parameter_variants(
        self,
        *,
        config: FactorPipelineConfig,
        base_experiment: FactorResearchExperiment,
        base_checkpoints: tuple[FactorCheckpointData, ...],
        decision_replay_plan: DecisionReplayPlan,
        evidence: tuple[FactorRobustnessParameterVariantEvidence, ...],
    ) -> tuple[FactorRobustnessParameterVariantResult, ...]:
        evidence_items = tuple(evidence)
        if not all(type(item) is FactorRobustnessParameterVariantEvidence for item in evidence_items):
            raise FactorResearchError("robustness parameter evidence 类型不正确")
        expected = config.robustness_plan.parameter_variants
        if tuple(sorted(evidence_items, key=lambda item: item.variant.variant_id)) != evidence_items:
            raise FactorResearchError("robustness parameter evidence 必须按 variant_id 排序")
        if tuple(item.variant for item in evidence_items) != expected:
            raise FactorResearchError("robustness parameter evidence 必须精确覆盖 frozen plan")
        thresholds = config.robustness_plan.stability_thresholds
        results: list[FactorRobustnessParameterVariantResult] = []
        for item in evidence_items:
            expected_config = config.with_parameter_variant(item.variant)
            self._validate_core_evidence(
                config=expected_config,
                experiment=item.experiment,
                decision_replay_plan=decision_replay_plan,
                market_evidences=item.market_evidences,
                computations=item.computations,
                checkpoints=item.checkpoint_data,
                proposals=item.proposals,
                outcomes=item.outcomes,
                expected_base_experiment=base_experiment,
                expected_base_checkpoints=base_checkpoints,
            )
            if item.analysis.factor_id != item.variant.factor_id:
                raise FactorResearchError("robustness parameter evidence 的 analysis 因子不匹配")
            analysis = analyze_factor(
                factor_id=item.variant.factor_id,
                exposures=tuple(
                    exposure
                    for checkpoint in item.checkpoint_data
                    for exposure in checkpoint.exposures
                ),
                outcomes=item.outcomes,
                quantile_count=expected_config.quantile_count,
                min_cross_section=expected_config.min_cross_section,
            )
            if item.analysis != analysis:
                raise FactorResearchError(
                    "robustness parameter evidence analysis 必须精确由该 PIT rerun 推导"
                )
            results.append(
                FactorRobustnessParameterVariantResult(
                    variant_id=item.variant.variant_id,
                    variant_hash=item.variant.variant_hash,
                    factor_id=item.variant.factor_id,
                    config_hash=expected_config.config_hash,
                    analysis_hash=analysis.analysis_hash,
                    analysis_period_count=len(analysis.periods),
                    mean_rank_ic=analysis.mean_rank_ic,
                    positive_ic_fraction=analysis.positive_ic_fraction,
                    quantile_spread=analysis.quantile_spread,
                    ic_standard_deviation=analysis.ic_standard_deviation,
                    mean_turnover=analysis.mean_turnover,
                    passed=_analysis_passes(analysis, thresholds),
                )
            )
        return tuple(sorted(results, key=lambda item: item.variant_id))

    def _evaluate_cost_scenarios(
        self,
        *,
        config: FactorPipelineConfig,
        checkpoints: tuple[FactorCheckpointData, ...],
        proposals: tuple[FactorPortfolioProposal, ...],
    ) -> tuple[FactorRobustnessCostScenarioResult, ...]:
        market_frame = build_factor_market_frame(checkpoints)
        target_frame = build_factor_target_frame(proposals, checkpoints)
        thresholds = config.robustness_plan.stability_thresholds
        results: list[FactorRobustnessCostScenarioResult] = []
        for scenario in config.robustness_plan.cost_scenarios:
            backtest = run_event_backtest(
                market_frame,
                target_frame,
                initial_cash=config.initial_cash,
                commission_bps=scenario.commission_bps,
                min_commission=scenario.min_commission,
                slippage_bps=scenario.slippage_bps,
                execution_delay_sessions=scenario.execution_delay_sessions,
            )
            results.append(
                FactorRobustnessCostScenarioResult(
                    scenario_id=scenario.scenario_id,
                    scenario_hash=scenario.scenario_hash,
                    backtest_result_hash=backtest.result_hash,
                    total_return=backtest.total_return,
                    max_drawdown=backtest.max_drawdown,
                    passed=(
                        backtest.total_return >= thresholds.minimum_cost_scenario_total_return
                        and backtest.max_drawdown
                        >= thresholds.minimum_cost_scenario_max_drawdown
                    ),
                )
            )
        return tuple(sorted(results, key=lambda item: item.scenario_id))

    def _factor_summaries(
        self,
        *,
        config: FactorPipelineConfig,
        scenario_results: tuple[FactorRobustnessScenarioResult, ...],
    ) -> tuple[FactorRobustnessFactorSummary, ...]:
        minimum_fraction = config.robustness_plan.stability_thresholds.minimum_scenario_pass_fraction
        summaries: list[FactorRobustnessFactorSummary] = []
        for definition in config.alpha_factors:
            entries = tuple(item for item in scenario_results if item.factor_id == definition.factor_id)
            if len(entries) != len(config.robustness_plan.subperiods):
                raise FactorResearchError("robustness scenario result 未完整覆盖 alpha factor")
            passed_count = sum(item.passed for item in entries)
            pass_fraction = passed_count / len(entries)
            summaries.append(
                FactorRobustnessFactorSummary(
                    factor_id=definition.factor_id,
                    scenario_count=len(entries),
                    passed_scenario_count=passed_count,
                    pass_fraction=pass_fraction,
                    passed=pass_fraction >= minimum_fraction,
                )
            )
        return tuple(sorted(summaries, key=lambda item: item.factor_id))


def _continuous_symbol_universe(
    checkpoints: tuple[FactorCheckpointData, ...],
) -> frozenset[str]:
    universe = frozenset(item.symbol for item in checkpoints[0].market_slices)
    if not universe:
        raise FactorResearchError("robustness checkpoint 不包含连续研究标的")
    for item in checkpoints[1:]:
        if frozenset(slice_.symbol for slice_ in item.market_slices) != universe:
            raise FactorResearchError("robustness 不接受变化的连续研究标的池")
    return universe


def _analysis_passes(
    analysis: FactorAnalysisResult,
    thresholds: FactorStabilityThresholds,
) -> bool:
    return (
        len(analysis.periods) >= thresholds.minimum_analysis_periods
        and analysis.mean_rank_ic >= thresholds.minimum_mean_rank_ic
        and analysis.positive_ic_fraction >= thresholds.minimum_positive_ic_fraction
        and analysis.quantile_spread >= thresholds.minimum_quantile_spread
        and analysis.ic_standard_deviation <= thresholds.maximum_ic_standard_deviation
        and analysis.mean_turnover <= thresholds.maximum_mean_turnover
    )


def _scenario_result(
    *,
    scenario_id: str,
    analysis: FactorAnalysisResult,
    thresholds: FactorStabilityThresholds,
) -> FactorRobustnessScenarioResult:
    return FactorRobustnessScenarioResult(
        scenario_id=scenario_id,
        factor_id=analysis.factor_id,
        analysis_hash=analysis.analysis_hash,
        analysis_period_count=len(analysis.periods),
        mean_rank_ic=analysis.mean_rank_ic,
        positive_ic_fraction=analysis.positive_ic_fraction,
        quantile_spread=analysis.quantile_spread,
        ic_standard_deviation=analysis.ic_standard_deviation,
        mean_turnover=analysis.mean_turnover,
        passed=_analysis_passes(analysis, thresholds),
    )


__all__ = [
    "FactorRobustnessEvaluator",
    "FactorRobustnessParameterVariantEvidence",
]
