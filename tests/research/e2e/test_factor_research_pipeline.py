"""End-to-end contracts for the research-only PIT factor pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

import polars as pl
import pytest

from northstar_quant.application.factor_mining_campaign import (
    FactorMiningCampaignArtifactMaterial,
    FactorMiningCampaignError,
    FactorMiningCampaignRunner,
)
from northstar_quant.application.durable_factor_mining_campaign import (
    DurableFactorMiningCampaignRunner,
    FactorMiningCampaignDurabilityError,
    FactorMiningCampaignRunRequest,
    LocalFactorMiningCampaignExecutionAdapter,
    PostgresFactorMiningCampaignLedger,
)
from northstar_quant.application.factor_mining_tools import (
    EvaluateFactorCandidateDiscoveryBatchRequest,
)
from northstar_quant.application.factor_research import FactorResearchPipeline
from northstar_quant.application.local_factor_research import (
    LocalFactorMiningResearchError,
    LocalFactorMiningResearchService,
)
from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.data.contracts.data_domain import DerivedArtifact
from northstar_quant.data.market.pit import (
    MarketDataKind,
    MarketDataPITError,
    MarketDataPITSpec,
)
from northstar_quant.research.backtest.models import BacktestEngine
from northstar_quant.research.factors import (
    FactorDefinition,
    FactorPipelineConfig,
    FactorRobustnessEvaluator,
    FactorRobustnessCostScenario,
    FactorRobustnessParameterVariant,
    FactorRobustnessPlan,
    FactorRobustnessSubperiod,
    FactorResearchError,
    FactorRole,
    FactorStabilityThresholds,
    ProposalStatus,
)
from northstar_quant.research.factor_mining import (
    CandidateDiscoveryDisposition,
    FactorCandidateGenerationReceipt,
    FactorCandidateGenerationRequest,
    FactorCandidateProposal,
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
from northstar_quant.foundation.db.repositories import (
    factor_mining_campaign_read_request_ledger,
)
from northstar_quant.research.factor_mining.run_bundle import (
    GovernedResearchArtifactKind,
    GovernedResearchArtifactReference,
    LocalFactorMiningRunBundle,
    LocalFactorMiningCampaignDeclaration,
    LocalFactorMiningRunBundleError,
    LocalFactorMiningRunConfig,
    LocalFactorMiningRunManifest,
)
from northstar_quant.research.factor_mining.artifact_bundle import (
    LoadedLocalFactorMiningRunBundle,
    LocalFactorMiningArtifactBundleError,
    LocalFactorMiningArtifactBundleStore,
    PublishedLocalFactorMiningArtifact,
)
from northstar_quant.research.validation.framework import (
    ValidationPeriod,
    ValidationSplit,
    ValidationStage,
    WalkForwardFold,
)
from northstar_quant.research.validation.lookahead import (
    DecisionReplayCheckpoint,
    DecisionReplayPlan,
)
from tests.helpers.pit_publication import publish_authorized_pit_dataset


_START = date(2026, 1, 5)
_SYMBOLS = ("AL_CONT", "CU_CONT", "RB_CONT", "ZN_CONT")
_DAILY_RETURNS = {
    "AL_CONT": (
        0.009,
        -0.006,
        0.014,
        0.004,
        -0.002,
        0.012,
        0.003,
        -0.009,
        0.011,
        0.006,
        0.005,
        -0.004,
        0.007,
        -0.006,
    ),
    "CU_CONT": (
        -0.004,
        0.011,
        0.002,
        -0.008,
        0.013,
        -0.001,
        0.007,
        0.004,
        -0.005,
        0.010,
        -0.003,
        0.008,
        -0.006,
        0.004,
    ),
    "RB_CONT": (
        0.013,
        0.003,
        -0.007,
        0.012,
        0.005,
        -0.006,
        0.009,
        0.002,
        0.014,
        -0.004,
        0.006,
        -0.005,
        0.003,
        0.009,
    ),
    "ZN_CONT": (
        0.001,
        0.016,
        -0.003,
        0.006,
        0.010,
        -0.008,
        0.004,
        0.012,
        -0.002,
        0.007,
        -0.004,
        0.006,
        -0.008,
        0.005,
    ),
}
_GOLDEN_FIXTURE = (
    Path(__file__).resolve().parents[1] / "golden" / "factor_research_pipeline_v1.json"
)
_FACTOR_MINING_PROTOCOL_GOLDEN_FIXTURE = (
    Path(__file__).resolve().parents[1] / "golden" / "factor_mining_protocol_v1.json"
)
_LOCAL_FACTOR_MINING_RUN_BUNDLE_GOLDEN_FIXTURE = (
    Path(__file__).resolve().parents[1] / "golden" / "local_factor_mining_run_bundle_v1.json"
)
_LOCAL_FACTOR_CODE_REVISION_HASH = sha256(
    b"northstar-factor-research-e2e-code-v1"
).hexdigest()


def _replace_first_utc_datetime_with_z(value: object) -> bool:
    """Make a semantically equivalent but noncanonical wire datetime for rejection tests."""

    if isinstance(value, dict):
        if set(value) == {"$datetime"}:
            raw = value["$datetime"]
            if isinstance(raw, str) and raw.endswith("+00:00"):
                value["$datetime"] = f"{raw[:-6]}Z"
                return True
        return any(_replace_first_utc_datetime_with_z(item) for item in value.values())
    if isinstance(value, list):
        return any(_replace_first_utc_datetime_with_z(item) for item in value)
    return False


def _days(count: int = 11) -> tuple[date, ...]:
    return tuple(_START + timedelta(days=offset) for offset in range(count))


def _decision_at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 16, tzinfo=UTC)


def _pit_spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close", "volume"),
        schema_version="cn_futures_feature_bar_v1",
    )


def _feature_bar_frame(
    days: tuple[date, ...],
    *,
    return_overrides: Mapping[tuple[str, int], float] | None = None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for symbol_index, symbol in enumerate(_SYMBOLS):
        close = 100.0 + 25.0 * symbol_index
        returns = _DAILY_RETURNS[symbol]
        for day_index, day in enumerate(days):
            if day_index:
                return_index = day_index - 1
                period_return = (
                    return_overrides.get((symbol, return_index), returns[return_index])
                    if return_overrides is not None
                    else returns[return_index]
                )
                close *= 1.0 + period_return
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "close": close,
                    "volume": 1_000.0 + 10.0 * symbol_index + day_index,
                    "available_at": _decision_at(day) - timedelta(minutes=1),
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(pl.Datetime("us", "UTC"))
    )


def _publish_checkpoint_plan(
    tmp_path: Path,
    *,
    count: int = 15,
    return_overrides: Mapping[tuple[str, int], float] | None = None,
) -> tuple[ArtifactStore, DecisionReplayPlan, tuple[date, ...]]:
    days = _days(count)
    store = ArtifactStore(tmp_path / "artifacts")
    checkpoints: list[DecisionReplayCheckpoint] = []
    spec = _pit_spec()
    for index, day in enumerate(days):
        _, dataset = publish_authorized_pit_dataset(
            tmp_path,
            _feature_bar_frame(days[: index + 1], return_overrides=return_overrides),
            dataset_id="factor_research_feature_bar",
            source_id="factor_research_fixture_source",
            adapter_id="factor-research-fixture-adapter",
            schema_version="cn_futures_feature_bar_v1",
            artifact_id=f"factor-research-checkpoint-{index:02d}",
            key_columns=("date", "symbol"),
            event_time_column="date",
            available_at_column="available_at",
            value_columns=("close", "volume"),
            normalized_available_at=_decision_at(day) - timedelta(minutes=1),
            store=store,
            scope_exchanges=("SHFE",),
            scope_products=("AL", "CU", "RB", "ZN"),
        )
        checkpoints.append(
            DecisionReplayCheckpoint(
                decision_at=_decision_at(day),
                decision_event_time=day,
                dataset_version_hash=dataset.version_hash,
                pit_spec=spec,
            )
        )
    return store, DecisionReplayPlan.create(checkpoints), days


def _robustness_plan(
    days: tuple[date, ...],
    *,
    parameter_variants: tuple[FactorRobustnessParameterVariant, ...],
    commission_bps: float,
    min_commission: float,
    slippage_bps: float,
    execution_delay_sessions: int,
) -> FactorRobustnessPlan:
    return FactorRobustnessPlan(
        plan_id="daily_factor_robustness",
        version="1.0.0",
        subperiods=(
            FactorRobustnessSubperiod(
                scenario_id="early",
                period=ValidationPeriod(days[3], days[5]),
                excluded_symbols=(),
            ),
            FactorRobustnessSubperiod(
                scenario_id="late_exclude_al",
                period=ValidationPeriod(days[6], days[10]),
                excluded_symbols=("AL_CONT",),
            ),
        ),
        parameter_variants=parameter_variants,
        cost_scenarios=(
            FactorRobustnessCostScenario(
                scenario_id="adverse",
                commission_bps=commission_bps * 2.0,
                min_commission=min_commission * 2.0,
                slippage_bps=slippage_bps * 2.0,
                execution_delay_sessions=execution_delay_sessions,
            ),
            FactorRobustnessCostScenario(
                scenario_id="baseline",
                commission_bps=commission_bps,
                min_commission=min_commission,
                slippage_bps=slippage_bps,
                execution_delay_sessions=execution_delay_sessions,
            ),
        ),
        stability_thresholds=FactorStabilityThresholds(
            minimum_analysis_periods=1,
            minimum_mean_rank_ic=-1.0,
            minimum_positive_ic_fraction=0.0,
            minimum_quantile_spread=-1.0,
            maximum_ic_standard_deviation=1.0,
            maximum_mean_turnover=10.0,
            minimum_scenario_pass_fraction=1.0,
            minimum_cost_scenario_total_return=-1.0,
            minimum_cost_scenario_max_drawdown=-1.0,
        ),
    )


def _factor_config(days: tuple[date, ...]) -> FactorPipelineConfig:
    return FactorPipelineConfig(
        pipeline_id="cn_futures_cross_sectional_factor",
        version="1.0.0",
        feature_version="1.0.0",
        code_revision="factor-research-e2e",
        factors=(
            FactorDefinition.create(
                factor_id="momentum_alpha",
                feature_id="momentum.roc",
                role=FactorRole.ALPHA,
                direction=1.0,
                risk_budget=0.5,
                parameters={"lookback_bars": 2},
            ),
            FactorDefinition.create(
                factor_id="realized_volatility",
                feature_id="technical.realized_volatility",
                role=FactorRole.RISK_MODEL,
                direction=1.0,
                risk_budget=0.0,
                parameters={"window_bars": 2},
            ),
            FactorDefinition.create(
                factor_id="volume_alpha",
                feature_id="technical.volume_ratio",
                role=FactorRole.ALPHA,
                direction=1.0,
                risk_budget=0.5,
                parameters={"window_bars": 2},
            ),
        ),
        volatility_factor_id="realized_volatility",
        min_cross_section=3,
        quantile_count=2,
        target_volatility=0.10,
        max_abs_weight=0.30,
        max_gross_exposure=0.80,
        holding_period_sessions=1,
        initial_cash=100_000.0,
        commission_bps=5.0,
        min_commission=1.0,
        slippage_bps=8.0,
        execution_delay_sessions=1,
        walk_forward_folds=(
            WalkForwardFold(
                fold_id="wf_01",
                split=ValidationSplit(
                    in_sample=ValidationPeriod(days[0], days[1]),
                    validation=ValidationPeriod(days[2], days[2]),
                    out_of_sample=ValidationPeriod(days[3], days[5]),
                ),
            ),
            WalkForwardFold(
                fold_id="wf_02",
                split=ValidationSplit(
                    in_sample=ValidationPeriod(days[3], days[4]),
                    validation=ValidationPeriod(days[5], days[5]),
                    out_of_sample=ValidationPeriod(days[6], days[8]),
                ),
            ),
        ),
        robustness_plan=_robustness_plan(
            days,
            parameter_variants=(
                FactorRobustnessParameterVariant.create(
                    variant_id="momentum_lookback_2",
                    factor_id="momentum_alpha",
                    parameters={"lookback_bars": 2},
                ),
                FactorRobustnessParameterVariant.create(
                    variant_id="momentum_lookback_3",
                    factor_id="momentum_alpha",
                    parameters={"lookback_bars": 3},
                ),
                FactorRobustnessParameterVariant.create(
                    variant_id="volume_window_2",
                    factor_id="volume_alpha",
                    parameters={"window_bars": 2},
                ),
                FactorRobustnessParameterVariant.create(
                    variant_id="volume_window_3",
                    factor_id="volume_alpha",
                    parameters={"window_bars": 3},
                ),
            ),
            commission_bps=5.0,
            min_commission=1.0,
            slippage_bps=8.0,
            execution_delay_sessions=1,
        ),
    )


def _factor_mining_folds(days: tuple[date, ...]) -> tuple[WalkForwardFold, WalkForwardFold]:
    """One globally isolated discovery span followed by two OOS folds.

    The day after validation is an embargo/maturity checkpoint.  It is not in
    any stage, which makes the selection time strictly later than the final
    validation outcome and strictly earlier than the first OOS decision.
    """

    return (
        WalkForwardFold(
            fold_id="wf_01",
            split=ValidationSplit(
                in_sample=ValidationPeriod(days[2], days[5]),
                validation=ValidationPeriod(days[6], days[9]),
                out_of_sample=ValidationPeriod(days[11], days[12]),
            ),
        ),
        WalkForwardFold(
            fold_id="wf_02",
            split=ValidationSplit(
                in_sample=ValidationPeriod(days[2], days[5]),
                validation=ValidationPeriod(days[6], days[9]),
                out_of_sample=ValidationPeriod(days[13], days[14]),
            ),
        ),
    )


def _factor_mining_campaign(
    *,
    config: FactorPipelineConfig,
    plan: DecisionReplayPlan,
    days: tuple[date, ...],
) -> FactorMiningCampaignSpec:
    return FactorMiningCampaignSpec(
        campaign_id="momentum_search",
        selection_at=plan.checkpoints[10].decision_at,
        decision_replay_plan_hash=plan.schedule_hash,
        dataset_version_hashes=tuple(
            sorted({checkpoint.dataset_version_hash for checkpoint in plan.checkpoints})
        ),
        template=FactorPipelineTemplate(
            template_id="daily_factor_mining",
            version=config.version,
            feature_version=config.feature_version,
            code_revision=config.code_revision,
            risk_model_factor=config.volatility_factor,
            min_cross_section=config.min_cross_section,
            quantile_count=config.quantile_count,
            target_volatility=config.target_volatility,
            max_abs_weight=config.max_abs_weight,
            max_gross_exposure=config.max_gross_exposure,
            holding_period_sessions=config.holding_period_sessions,
            initial_cash=config.initial_cash,
            commission_bps=config.commission_bps,
            min_commission=config.min_commission,
            slippage_bps=config.slippage_bps,
            execution_delay_sessions=config.execution_delay_sessions,
            walk_forward_folds=_factor_mining_folds(days),
            robustness_plan=_robustness_plan(
                days,
                parameter_variants=(
                    FactorRobustnessParameterVariant.create(
                        variant_id="candidate_lookback_1",
                        factor_id="candidate_alpha",
                        parameters={"lookback_bars": 1},
                    ),
                    FactorRobustnessParameterVariant.create(
                        variant_id="candidate_lookback_2",
                        factor_id="candidate_alpha",
                        parameters={"lookback_bars": 2},
                    ),
                    FactorRobustnessParameterVariant.create(
                        variant_id="candidate_lookback_3",
                        factor_id="candidate_alpha",
                        parameters={"lookback_bars": 3},
                    ),
                ),
                commission_bps=config.commission_bps,
                min_commission=config.min_commission,
                slippage_bps=config.slippage_bps,
                execution_delay_sessions=config.execution_delay_sessions,
            ),
        ),
        primitives=(
            FactorPrimitive(
                primitive_id="momentum_roc",
                feature_id="momentum.roc",
                allowed_directions=(-1.0, 1.0),
                parameter_domains=(
                    FactorParameterDomain(
                        name="lookback_bars",
                        allowed_values=(1, 2, 3),
                    ),
                ),
            ),
        ),
        budget=FactorSearchBudget(max_candidates=2),
        selection_policy=FactorMiningSelectionPolicy(
            policy_id="discovery_policy_v1",
            cost_scenarios=(
                FactorMiningCostScenario(
                    scenario_id="adverse",
                    commission_bps=10.0,
                    min_commission=2.0,
                    slippage_bps=16.0,
                    execution_delay_sessions=1,
                ),
                FactorMiningCostScenario(
                    scenario_id="baseline",
                    commission_bps=config.commission_bps,
                    min_commission=config.min_commission,
                    slippage_bps=config.slippage_bps,
                    execution_delay_sessions=config.execution_delay_sessions,
                ),
            ),
            minimum_in_sample_periods=3,
            minimum_validation_periods=3,
            minimum_stage_backtest_sessions=2,
            minimum_in_sample_mean_rank_ic=-1.0,
            minimum_validation_mean_rank_ic=-1.0,
            minimum_validation_quantile_spread=-1.0,
            maximum_validation_factor_turnover=10.0,
            maximum_validation_portfolio_turnover=10.0,
            minimum_validation_total_return=-0.99,
            minimum_validation_max_drawdown=-1.0,
            family_wise_alpha=0.99,
            multiple_testing_control=FactorMiningMultipleTestingControl.BONFERRONI_SIGN_TEST,
            max_selected_candidates=1,
            stage_boundary_mode=FactorMiningStageBoundaryMode.FLAT_START_FORCED_CLOSE,
        ),
        generator_id="deterministic_e2e_generator",
        generator_model_revision_hash=sha256(b"deterministic-e2e-model").hexdigest(),
        prompt_template_hash=sha256(b"factor-mining-prompt").hexdigest(),
    )


def _factor_mining_receipt(
    campaign: FactorMiningCampaignSpec,
) -> FactorCandidateGenerationReceipt:
    long_proposal = FactorCandidateProposal.create(
        campaign_id=campaign.campaign_id,
        candidate_id="momentum_two_bar_long",
        primitive_id="momentum_roc",
        direction=1.0,
        parameters={"lookback_bars": 2},
    )
    short_proposal = FactorCandidateProposal.create(
        campaign_id=campaign.campaign_id,
        candidate_id="momentum_two_bar_short",
        primitive_id="momentum_roc",
        direction=-1.0,
        parameters={"lookback_bars": 2},
    )
    return FactorCandidateGenerationReceipt(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        generator_id=campaign.generator_id,
        generator_model_revision_hash=campaign.generator_model_revision_hash,
        prompt_template_hash=campaign.prompt_template_hash,
        provider_output_hash=sha256(b"deterministic-provider-output").hexdigest(),
        proposals=(long_proposal, short_proposal),
    )


class _FixedFactorCandidateGenerator:
    """Trusted test double for the provider edge, not for the local worker."""

    def __init__(self, *, receipt: FactorCandidateGenerationReceipt) -> None:
        self.receipt = receipt
        self.requests: list[FactorCandidateGenerationRequest] = []

    def generate(
        self,
        request: FactorCandidateGenerationRequest,
    ) -> FactorCandidateGenerationReceipt:
        self.requests.append(request)
        return self.receipt


def _durable_factor_mining_declaration(
    *,
    campaign: FactorMiningCampaignSpec,
    plan: DecisionReplayPlan,
    max_artifact_bytes: int,
) -> LocalFactorMiningCampaignDeclaration:
    return LocalFactorMiningCampaignDeclaration(
        dataset_version_hashes=campaign.dataset_version_hashes,
        plan=plan,
        campaign=campaign,
        config=LocalFactorMiningRunConfig(
            config_id="durable_factor_mining_e2e",
            code_revision=campaign.template.code_revision,
            code_revision_hash=_LOCAL_FACTOR_CODE_REVISION_HASH,
            output_schema_version="local_factor_research_v1",
            output_transform_version="local_factor_research_v1",
            retention_days=3650,
            automatic_cleanup=False,
        ),
        runner_budget=FactorMiningRunnerResourceBudget(
            max_candidates=campaign.budget.max_candidates,
            max_concurrent_runs=1,
            max_cpu_seconds=86_400,
            max_memory_bytes=1 << 40,
            max_wall_clock_seconds=86_400,
            max_data_rows=1_000_000_000,
            max_artifact_bytes=max_artifact_bytes,
        ),
    )


def _durable_factor_mining_runner(
    *,
    store: ArtifactStore,
    generator: _FixedFactorCandidateGenerator,
    postgresql_session_factory,
) -> DurableFactorMiningCampaignRunner:
    return DurableFactorMiningCampaignRunner(
        ledger=PostgresFactorMiningCampaignLedger(
            session_factory=postgresql_session_factory,
        ),
        execution=LocalFactorMiningCampaignExecutionAdapter(
            artifact_store=store,
            generator=generator,
        ),
    )


@pytest.mark.e2e
@pytest.mark.regression
def test_factor_research_pipeline_replays_strict_pit_inputs_and_stays_research_only(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path, count=11)
    config = _factor_config(days)

    costly = FactorResearchPipeline(artifact_store=store, config=config).run(plan=plan)
    replayed = FactorResearchPipeline(artifact_store=store, config=config).run(plan=plan)
    cost_free_config = replace(
        config,
        commission_bps=0.0,
        min_commission=0.0,
        slippage_bps=0.0,
        robustness_plan=_robustness_plan(
            days,
            parameter_variants=config.robustness_plan.parameter_variants,
            commission_bps=0.0,
            min_commission=0.0,
            slippage_bps=0.0,
            execution_delay_sessions=config.execution_delay_sessions,
        ),
    )
    cost_free = FactorResearchPipeline(artifact_store=store, config=cost_free_config).run(plan=plan)

    assert len(costly.checkpoint_data) == len(plan.checkpoints)
    assert tuple(item.dataset_version_hash for item in costly.checkpoint_data) == tuple(
        item.dataset_version_hash for item in plan.checkpoints
    )
    assert all(len(item.materializations) == 3 for item in costly.checkpoint_data)
    assert all(
        {reference.factor_id for reference in item.materializations}
        == {"momentum_alpha", "realized_volatility", "volume_alpha"}
        for item in costly.checkpoint_data
    )
    assert all(
        feature.replay_materialization is not None
        and feature.replay_materialization.lineage.decision_time_safe is True
        and feature.replay_materialization.lineage.replay_checkpoint_hash
        == report.evidence.market_data.checkpoint.checkpoint_hash
        for report in costly.lookahead_certificate.reports
        for feature in report.evidence.features
    )
    assert all(report.is_safe for report in costly.lookahead_certificate.reports)

    assert costly.candidate_admission_eligible is False
    assert costly.simnow_handoff_allowed is False
    assert costly.research_only is True
    assert all(proposal.candidate_admission_eligible is False for proposal in costly.proposals)
    assert all(proposal.simnow_handoff_allowed is False for proposal in costly.proposals)
    assert all(proposal.research_only is True for proposal in costly.proposals)
    assert any(proposal.status is ProposalStatus.PROPOSAL for proposal in costly.proposals)
    assert costly.experiment.config_hash == config.config_hash
    assert costly.experiment.decision_replay_plan_hash == plan.schedule_hash
    assert costly.experiment.dataset_version_hashes == tuple(
        sorted(item.dataset_version_hash for item in plan.checkpoints)
    )
    assert costly.experiment.code_revision == config.code_revision
    assert costly.experiment.research_only is True
    assert costly.experiment.candidate_admission_eligible is False
    assert costly.experiment.simnow_handoff_allowed is False
    assert costly.manifest.experiment_hash == costly.experiment.experiment_hash
    assert costly.manifest.feature_version_hashes == costly.experiment.feature_version_hashes
    assert costly.manifest.code_revision == config.code_revision
    assert costly.manifest.research_only is True
    assert costly.manifest.candidate_admission_eligible is False
    assert costly.manifest.simnow_handoff_allowed is False

    assert costly.backtest.engine is BacktestEngine.WEIGHT_RETURN
    assert costly.backtest.turnover_estimate > 0.0
    assert costly.backtest.total_return < cost_free.backtest.total_return
    assert len(costly.walk_forward) >= 2
    assert tuple(item.fold_id for item in costly.walk_forward) == ("wf_01", "wf_02")
    assert all(item.session_count >= 2 for item in costly.walk_forward)

    assert tuple(item.factor_id for item in costly.analyses) == ("momentum_alpha", "volume_alpha")
    assert all(item.quantile_count == config.quantile_count for item in costly.analyses)
    assert all(item.periods for item in costly.analyses)
    assert all(
        tuple(bucket for bucket, _ in period.quantile_returns) == (1, 2)
        for analysis in costly.analyses
        for period in analysis.periods
    )
    assert costly.robustness.plan_hash == config.robustness_plan.plan_hash
    assert costly.robustness.config_hash == config.config_hash
    assert costly.robustness.passed is True
    assert {item.scenario_id for item in costly.robustness.cost_scenario_results} == {
        "adverse",
        "baseline",
    }
    assert {
        (item.scenario_id, item.factor_id)
        for item in costly.robustness.scenario_results
    } == {
        ("early", "momentum_alpha"),
        ("early", "volume_alpha"),
        ("late_exclude_al", "momentum_alpha"),
        ("late_exclude_al", "volume_alpha"),
    }
    assert tuple(item.variant_id for item in costly.robustness.parameter_variant_results) == (
        "momentum_lookback_2",
        "momentum_lookback_3",
        "volume_window_2",
        "volume_window_3",
    )
    assert costly.manifest.robustness_plan_hash == config.robustness_plan.plan_hash
    assert costly.manifest.robustness_result_hash == costly.robustness.result_hash
    with pytest.raises(FactorResearchError, match="manifest 必须精确绑定"):
        replace(
            costly,
            manifest=replace(
                costly.manifest,
                robustness_result_hash=sha256(b"foreign-robustness-result").hexdigest(),
            ),
        )

    assert costly.manifest.manifest_hash == replayed.manifest.manifest_hash
    assert costly.manifest == replayed.manifest
    assert costly.backtest.result_hash == replayed.backtest.result_hash
    assert costly.lookahead_certificate.certificate_hash == replayed.lookahead_certificate.certificate_hash
    golden = json.loads(_GOLDEN_FIXTURE.read_text(encoding="utf-8"))
    assert golden == {
        "analysis_hashes": [item.analysis_hash for item in costly.analyses],
        "backtest_result_hash": costly.backtest.result_hash,
        "experiment_hash": costly.experiment.experiment_hash,
        "format": "northstar.factor-research-golden.v1",
        "lookahead_certificate_hash": costly.lookahead_certificate.certificate_hash,
        "manifest_hash": costly.manifest.manifest_hash,
        "robustness_plan_hash": costly.robustness.plan_hash,
        "robustness_result_hash": costly.robustness.result_hash,
        "walk_forward_result_hashes": [item.result_hash for item in costly.walk_forward],
    }


@pytest.mark.e2e
@pytest.mark.failure
def test_factor_robustness_rejects_unknown_symbol_exclusions_after_pit_replay(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path, count=11)
    config = _factor_config(days)
    unknown_symbol_scenario = replace(
        config.robustness_plan.subperiods[1],
        excluded_symbols=("UNKNOWN_CONT",),
    )
    unknown_symbol_plan = replace(
        config.robustness_plan,
        subperiods=(config.robustness_plan.subperiods[0], unknown_symbol_scenario),
    )

    with pytest.raises(FactorResearchError, match="不在连续研究品种池"):
        FactorResearchPipeline(
            artifact_store=store,
            config=replace(config, robustness_plan=unknown_symbol_plan),
        ).run(plan=plan)


@pytest.mark.e2e
@pytest.mark.failure
def test_factor_robustness_rejects_foreign_pit_or_parameter_rerun_evidence(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path, count=11)
    config = _factor_config(days)
    pipeline = FactorResearchPipeline(artifact_store=store, config=config)
    core = pipeline._run_core(plan)
    evidence = tuple(
        pipeline._parameter_variant_evidence(plan=plan, variant=variant)
        for variant in config.robustness_plan.parameter_variants
    )
    evaluator = FactorRobustnessEvaluator(artifact_store=store)
    kwargs = {
        "config": config,
        "experiment": core.experiment,
        "decision_replay_plan": plan,
        "market_evidences": core.market_evidences,
        "computations": core.computations,
        "checkpoint_data": core.checkpoint_data,
        "proposals": core.proposals,
        "outcomes": core.outcomes,
        "parameter_variant_evidence": evidence,
    }

    with pytest.raises(FactorResearchError, match="market evidence 必须由 immutable store"):
        evaluator.evaluate(
            **{
                **kwargs,
                "market_evidences": tuple(reversed(core.market_evidences)),
            }
        )

    with pytest.raises(FactorResearchError, match="computation.data 必须精确匹配 checkpoint_data"):
        evaluator.evaluate(
            **{
                **kwargs,
                "parameter_variant_evidence": (
                    evidence[0],
                    replace(evidence[1], checkpoint_data=core.checkpoint_data),
                    *evidence[2:],
                ),
            }
        )

    with pytest.raises(FactorResearchError, match="analysis 必须精确由该 PIT rerun 推导"):
        evaluator.evaluate(
            **{
                **kwargs,
                "parameter_variant_evidence": (
                    evidence[0],
                    replace(evidence[1], analysis=evidence[0].analysis),
                    *evidence[2:],
                ),
            }
        )

    parameter_neighbour = next(
        item
        for item in evidence
        if item.variant.variant_id == "momentum_lookback_3"
    )
    base_computation = core.computations[0]
    neighbour_computation = parameter_neighbour.computations[0]
    foreign_materialization = dict(neighbour_computation.materializations)["momentum_alpha"]
    base_data = core.checkpoint_data[0]
    momentum_definition = next(
        item for item in config.factors if item.factor_id == "momentum_alpha"
    )
    rebound_references = tuple(
        replace(reference, materialization_hash=foreign_materialization.materialization_hash)
        if reference.factor_id == "momentum_alpha"
        else reference
        for reference in base_data.materializations
    )
    rebound_exposures = tuple(
        sorted(
            (
                *(item for item in base_data.exposures if item.factor_id != "momentum_alpha"),
                *(
                    replace(
                        item,
                        factor_definition_hash=momentum_definition.definition_hash,
                        config_hash=config.config_hash,
                    )
                    for item in neighbour_computation.data.exposures
                    if item.factor_id == "momentum_alpha"
                ),
            ),
            key=lambda item: (item.factor_id, item.symbol),
        )
    )
    rebound_data = replace(
        base_data,
        materializations=rebound_references,
        exposures=rebound_exposures,
    )
    rebound_computation = replace(
        base_computation,
        data=rebound_data,
        materializations=tuple(
            (factor_id, foreign_materialization)
            if factor_id == "momentum_alpha"
            else (factor_id, materialization)
            for factor_id, materialization in base_computation.materializations
        ),
    )
    with pytest.raises(FactorResearchError, match="canonical feature、参数与 PIT snapshot"):
        evaluator.evaluate(
            **{
                **kwargs,
                "checkpoint_data": (rebound_data, *core.checkpoint_data[1:]),
                "computations": (rebound_computation, *core.computations[1:]),
            }
        )


@pytest.mark.e2e
@pytest.mark.failure
def test_factor_research_run_rejects_foreign_lookahead_certificates(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path, count=11)
    config = _factor_config(days)
    pipeline = FactorResearchPipeline(artifact_store=store, config=config)
    run = pipeline.run(plan=plan)

    foreign_plan_certificate = pipeline._replay(
        DecisionReplayPlan.create(plan.checkpoints[:-1])
    ).lookahead_certificate
    with pytest.raises(FactorResearchError, match="decision replay plan"):
        replace(
            run,
            lookahead_certificate=foreign_plan_certificate,
            manifest=replace(
                run.manifest,
                lookahead_certificate_hash=foreign_plan_certificate.certificate_hash,
            ),
        )

    foreign_target_certificate = FactorResearchPipeline(
        artifact_store=store,
        config=replace(config, initial_cash=110_000.0),
    )._replay(plan).lookahead_certificate
    with pytest.raises(FactorResearchError, match="checkpoint 与 proposal"):
        replace(
            run,
            lookahead_certificate=foreign_target_certificate,
            manifest=replace(
                run.manifest,
                lookahead_certificate_hash=foreign_target_certificate.certificate_hash,
            ),
        )


@pytest.mark.e2e
@pytest.mark.regression
def test_factor_mining_campaign_withholds_oos_until_a_researcher_commits_and_releases(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    receipt = _factor_mining_receipt(campaign)

    first = FactorMiningCampaignRunner(
        artifact_store=store,
        campaign=campaign,
        plan=plan,
    )
    first = first.evaluate_discovery_candidate_batch(
        request=EvaluateFactorCandidateDiscoveryBatchRequest(generation=receipt)
    )
    replayed = FactorMiningCampaignRunner(
        artifact_store=store,
        campaign=campaign,
        plan=plan,
    ).evaluate_discovery_candidate_batch(
        request=EvaluateFactorCandidateDiscoveryBatchRequest(generation=receipt)
    )

    assert first.discovery_result_hash == replayed.discovery_result_hash
    assert first.results == replayed.results
    assert all(
        result.disposition is CandidateDiscoveryDisposition.DISCOVERY_EVALUATED
        for result in first.results
    )
    assert all(
        {evidence.stage for evidence in result.stage_evidence}
        == {ValidationStage.IN_SAMPLE, ValidationStage.VALIDATION}
        for result in first.results
    )
    assert all(not hasattr(result, "run_manifest_hash") for result in first.results)
    assert all(not hasattr(result, "walk_forward_result_hashes") for result in first.results)
    assert first.research_only is True
    assert first.candidate_admission_eligible is False
    assert first.simnow_handoff_allowed is False

    runner = FactorMiningCampaignRunner(
        artifact_store=store,
        campaign=campaign,
        plan=plan,
    )
    discovery = runner.evaluate_discovery_candidate_batch(
        request=EvaluateFactorCandidateDiscoveryBatchRequest(generation=receipt)
    )
    commitment = runner.commit_selection(discovery=discovery)
    assert len(commitment.selected_records) == 1
    assert commitment.research_only is True
    release = runner.release_oos(commitment=commitment)
    assert release.research_only is True
    assert all(
        {evidence.stage for evidence in result.stage_evidence} == {ValidationStage.OUT_OF_SAMPLE}
        for result in release.results
    )
    mining_golden = json.loads(_FACTOR_MINING_PROTOCOL_GOLDEN_FIXTURE.read_text(encoding="utf-8"))
    assert mining_golden == {
        "discovery_result_hash": discovery.discovery_result_hash,
        "format": "northstar.factor-mining-protocol-golden.v1",
        "oos_release_hash": release.release_hash,
        "oos_result_hashes": [result.release_result_hash for result in release.results],
        "selection_commitment_hash": commitment.commitment_hash,
        "selection_records": [
            {
                "candidate_id": record.candidate_id,
                "discovery_score": record.discovery_score,
                "disposition": record.disposition.value,
                "rank": record.rank,
                "reason_code": record.reason_code,
            }
            for record in commitment.records
        ],
    }
    with pytest.raises(FactorMiningCampaignError, match="cannot release OOS more than once"):
        runner.release_oos(commitment=commitment)


@pytest.mark.e2e
@pytest.mark.regression
def test_receipt_free_campaign_declaration_is_governed_before_generation(
    tmp_path: Path,
) -> None:
    """The durable boundary has a typed, governed input before provider work."""

    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    config = LocalFactorMiningRunConfig(
        config_id="durable_campaign_declaration",
        code_revision=campaign.template.code_revision,
        code_revision_hash=_LOCAL_FACTOR_CODE_REVISION_HASH,
        output_schema_version="local_factor_research_v1",
        output_transform_version="local_factor_research_v1",
        retention_days=3650,
        automatic_cleanup=False,
    )
    budget = FactorMiningRunnerResourceBudget(
        max_candidates=campaign.budget.max_candidates,
        max_concurrent_runs=1,
        max_cpu_seconds=600,
        max_memory_bytes=1 << 30,
        max_wall_clock_seconds=900,
        max_data_rows=100_000,
        max_artifact_bytes=50_000_000,
    )
    declaration = LocalFactorMiningCampaignDeclaration(
        dataset_version_hashes=campaign.dataset_version_hashes,
        plan=plan,
        campaign=campaign,
        config=config,
        runner_budget=budget,
    )
    bundle_store = LocalFactorMiningArtifactBundleStore(artifact_store=store)
    published = bundle_store.publish_campaign_declaration(declaration=declaration)
    loaded = bundle_store.load_campaign_declaration(published.stored.snapshot.snapshot_hash)

    assert loaded.declaration == declaration
    assert loaded.stored.parent_snapshot_hashes
    bundle = LocalFactorMiningRunBundle.from_campaign_declaration(
        declaration=loaded.declaration,
        generation=_factor_mining_receipt(campaign),
    )
    assert bundle.campaign == campaign
    assert bundle.generation.receipt_hash == _factor_mining_receipt(campaign).receipt_hash

    with pytest.raises(LocalFactorMiningRunBundleError, match="must exactly match"):
        LocalFactorMiningCampaignDeclaration(
            dataset_version_hashes=campaign.dataset_version_hashes,
            plan=plan,
            campaign=campaign,
            config=config,
            runner_budget=replace(budget, max_candidates=campaign.budget.max_candidates + 1),
        )

    with pytest.raises(LocalFactorMiningRunBundleError, match="must exactly match"):
        LocalFactorMiningCampaignDeclaration(
            dataset_version_hashes=campaign.dataset_version_hashes,
            plan=plan,
            campaign=campaign,
            config=config,
            runner_budget=replace(budget, max_candidates=campaign.budget.max_candidates - 1),
        )


@pytest.mark.e2e
@pytest.mark.integration
def test_durable_campaign_runner_uses_the_real_local_adapter_and_hash_linked_postgres_ledger(
    tmp_path: Path,
    postgresql_session_factory,
) -> None:
    """The concrete adapter completes the only valid durable research chain."""

    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    declaration = _durable_factor_mining_declaration(
        campaign=campaign,
        plan=plan,
        max_artifact_bytes=50_000_000,
    )
    declaration_snapshot_hash = LocalFactorMiningArtifactBundleStore(
        artifact_store=store
    ).publish_campaign_declaration(declaration=declaration).stored.snapshot.snapshot_hash
    request = FactorMiningCampaignRunRequest(
        run_id="durable_factor_mining_success_1",
        actor_id="researcher:1",
        declaration_snapshot_hash=declaration_snapshot_hash,
    )
    generator = _FixedFactorCandidateGenerator(receipt=_factor_mining_receipt(campaign))

    result = _durable_factor_mining_runner(
        store=store,
        generator=generator,
        postgresql_session_factory=postgresql_session_factory,
    ).run(request)

    assert [item.campaign.campaign_hash for item in generator.requests] == [campaign.campaign_hash]
    assert result.request == request
    assert result.execution.selected_candidate_count == 1
    definition = LocalFactorMiningArtifactBundleStore(artifact_store=store).load_definition(
        result.execution.bundle_snapshot_hash
    )
    assert result.execution.resource_usage.artifact_byte_count > definition.stored.byte_length

    with postgresql_session_factory() as session:
        ledger = factor_mining_campaign_read_request_ledger(
            session,
            request_id=request.run_id,
        )

    assert ledger is not None
    assert ledger.campaign.declaration_hash == declaration.declaration_hash
    assert ledger.campaign.declaration_snapshot_hash == declaration_snapshot_hash
    assert tuple(event.event_kind for event in ledger.events) == (
        "RESERVED",
        "RECEIPT_RECORDED",
        "DISCOVERY_RECORDED",
        "SELECTION_COMMITTED",
        "OOS_RESERVED",
        "OOS_RELEASED",
        "RESULT_RECORDED",
    )
    assert ledger.events[-1].is_terminal is True
    assert ledger.events[-1].artifact_byte_count == result.execution.resource_usage.artifact_byte_count
    assert all(event.request_actor_id == request.actor_id for event in ledger.events)
    assert all(
        event.predecessor_record_hash == previous.record_hash
        for previous, event in zip(ledger.events, ledger.events[1:])
    )


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.failure
def test_durable_campaign_leaves_post_oos_partial_failure_unresolved_with_the_real_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    postgresql_session_factory,
) -> None:
    """A failure after real OOS release cannot be rewritten as a terminal fact."""

    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    declaration = _durable_factor_mining_declaration(
        campaign=campaign,
        plan=plan,
        max_artifact_bytes=50_000_000,
    )
    declaration_snapshot_hash = LocalFactorMiningArtifactBundleStore(
        artifact_store=store
    ).publish_campaign_declaration(declaration=declaration).stored.snapshot.snapshot_hash
    request = FactorMiningCampaignRunRequest(
        run_id="durable_factor_mining_oos_partial_1",
        actor_id="researcher:1",
        declaration_snapshot_hash=declaration_snapshot_hash,
    )
    generator = _FixedFactorCandidateGenerator(receipt=_factor_mining_receipt(campaign))
    original_prepare_release = LocalFactorMiningResearchService.prepare_release

    def _raise_after_real_oos_release(
        service: LocalFactorMiningResearchService,
        *,
        preparation,
    ):
        prepared = original_prepare_release(service, preparation=preparation)
        assert prepared.oos_release_hash is not None
        raise LocalFactorMiningResearchError("intentional post-OOS worker interruption")

    monkeypatch.setattr(
        LocalFactorMiningResearchService,
        "prepare_release",
        _raise_after_real_oos_release,
    )

    with pytest.raises(
        FactorMiningCampaignDurabilityError,
        match="FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED",
    ):
        _durable_factor_mining_runner(
            store=store,
            generator=generator,
            postgresql_session_factory=postgresql_session_factory,
        ).run(request)

    assert [item.campaign.campaign_hash for item in generator.requests] == [campaign.campaign_hash]
    with postgresql_session_factory() as session:
        ledger = factor_mining_campaign_read_request_ledger(
            session,
            request_id=request.run_id,
        )

    assert ledger is not None
    assert tuple(event.event_kind for event in ledger.events) == (
        "RESERVED",
        "RECEIPT_RECORDED",
        "DISCOVERY_RECORDED",
        "SELECTION_COMMITTED",
        "OOS_RESERVED",
    )
    assert ledger.events[-1].is_terminal is False
    assert {event.event_kind for event in ledger.events}.isdisjoint(
        {"OOS_RELEASED", "RESULT_RECORDED", "FAILED"}
    )


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.failure
def test_durable_campaign_counts_receipt_bound_definition_bytes_before_writing_or_discovery(
    tmp_path: Path,
    postgresql_session_factory,
) -> None:
    """Definition bytes are a pre-publication resource cost, not hidden output."""

    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    receipt = _factor_mining_receipt(campaign)
    unbounded_declaration = _durable_factor_mining_declaration(
        campaign=campaign,
        plan=plan,
        max_artifact_bytes=50_000_000,
    )
    definition_byte_count = len(
        LocalFactorMiningRunBundle.from_campaign_declaration(
            declaration=unbounded_declaration,
            generation=receipt,
        ).to_bytes()
    )
    declaration = _durable_factor_mining_declaration(
        campaign=campaign,
        plan=plan,
        max_artifact_bytes=definition_byte_count - 1,
    )
    declaration_snapshot_hash = LocalFactorMiningArtifactBundleStore(
        artifact_store=store
    ).publish_campaign_declaration(declaration=declaration).stored.snapshot.snapshot_hash
    request = FactorMiningCampaignRunRequest(
        run_id="durable_factor_mining_definition_budget_1",
        actor_id="researcher:1",
        declaration_snapshot_hash=declaration_snapshot_hash,
    )
    generator = _FixedFactorCandidateGenerator(receipt=receipt)

    with pytest.raises(
        FactorMiningCampaignDurabilityError,
        match=(
            "FACTOR_MINING_CAMPAIGN_EXECUTION_"
            "FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED"
        ),
    ):
        _durable_factor_mining_runner(
            store=store,
            generator=generator,
            postgresql_session_factory=postgresql_session_factory,
        ).run(request)

    assert [item.campaign.campaign_hash for item in generator.requests] == [campaign.campaign_hash]
    with postgresql_session_factory() as session:
        ledger = factor_mining_campaign_read_request_ledger(
            session,
            request_id=request.run_id,
        )

    assert ledger is not None
    assert tuple(event.event_kind for event in ledger.events) == (
        "RESERVED",
        "RECEIPT_RECORDED",
        "FAILED",
    )
    assert ledger.events[-1].artifact_byte_count == definition_byte_count
    assert ledger.events[-1].artifact_byte_count > declaration.runner_budget.max_artifact_bytes
    assert {event.event_kind for event in ledger.events}.isdisjoint(
        {"DISCOVERY_RECORDED", "SELECTION_COMMITTED", "OOS_RESERVED", "RESULT_RECORDED"}
    )


@pytest.mark.e2e
@pytest.mark.regression
def test_local_factor_mining_bundle_runs_replays_and_publishes_governed_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    bundle = LocalFactorMiningRunBundle(
        dataset_version_hashes=tuple(
            sorted({checkpoint.dataset_version_hash for checkpoint in plan.checkpoints})
        ),
        plan=plan,
        campaign=campaign,
        generation=_factor_mining_receipt(campaign),
        config=LocalFactorMiningRunConfig(
            config_id="local_factor_evidence",
            code_revision=campaign.template.code_revision,
            code_revision_hash=_LOCAL_FACTOR_CODE_REVISION_HASH,
            output_schema_version="local_factor_research_v1",
            output_transform_version="local_factor_research_v1",
            retention_days=3650,
            automatic_cleanup=False,
        ),
    )
    service = LocalFactorMiningResearchService(artifact_store=store)

    bundle_snapshot_hash = service.publish_definition(bundle=bundle)
    definition = service.inspect(artifact_snapshot_hash=bundle_snapshot_hash)
    before_prepare = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    preparation = service.prepare(bundle_snapshot_hash=bundle_snapshot_hash)
    after_prepare = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    assert after_prepare == before_prepare
    assert preparation.artifact_byte_count > 0
    first = service.publish(preparation=preparation)
    with pytest.raises(LocalFactorMiningResearchError, match="already consumed"):
        service.publish(preparation=preparation)
    before_replay = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    replayed = service.replay(
        bundle_snapshot_hash=bundle_snapshot_hash,
        expected_manifest_snapshot_hash=first.manifest_snapshot_hash,
    )
    after_replay = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    original_evaluate = LocalFactorMiningResearchService._evaluate_loaded_bundle

    def _tampered_evaluate(
        service: LocalFactorMiningResearchService,
        *,
        loaded: LoadedLocalFactorMiningRunBundle,
    ) -> FactorMiningCampaignArtifactMaterial:
        material = original_evaluate(service, loaded=loaded)
        first_candidate, *remaining_candidates = material.candidates
        return replace(
            material,
            candidates=(
                replace(first_candidate, candidate_id=f"tampered-{first_candidate.candidate_id}"),
                *remaining_candidates,
            ),
        )

    monkeypatch.setattr(
        LocalFactorMiningResearchService,
        "_evaluate_loaded_bundle",
        _tampered_evaluate,
    )
    with pytest.raises(
        LocalFactorMiningResearchError,
        match="does not reproduce the exact governed manifest",
    ):
        service.replay(
            bundle_snapshot_hash=bundle_snapshot_hash,
            expected_manifest_snapshot_hash=first.manifest_snapshot_hash,
        )
    after_tampered_replay = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    loaded = service._bundle_store.load_definition(bundle_snapshot_hash)
    references = {item.kind: item for item in first.manifest.artifacts}
    selection_reference = references[GovernedResearchArtifactKind.SELECTION_EVIDENCE]
    discovery_reference = references[GovernedResearchArtifactKind.DISCOVERY_EVIDENCE]
    report_reference = references[GovernedResearchArtifactKind.REPORT]
    original_discovery = store.load_artifact_value(discovery_reference.snapshot_hash)
    original_report = store.load_artifact_value(report_reference.snapshot_hash)
    assert type(original_discovery) is DerivedArtifact
    assert type(original_report) is DerivedArtifact
    report_payload = json.loads(store.read_payload(report_reference.snapshot_hash))
    assert "continuous_daily_series_not_actual_contract_execution" in report_payload["content"][
        "limitations"
    ]
    assert report_payload["content"]["robustness"]
    assert all(
        len(item["plan_hash"]) == len(item["result_hash"]) == 64
        and type(item["passed"]) is bool
        for item in report_payload["content"]["robustness"]
    )
    analysis_reference = references[GovernedResearchArtifactKind.ANALYSES]
    analysis_payload = json.loads(store.read_payload(analysis_reference.snapshot_hash))
    analysis_candidates = analysis_payload["content"]["candidates"]
    assert isinstance(analysis_candidates, list)
    assert all(
        isinstance(candidate, dict)
        and isinstance(candidate["oos_full_run"], dict)
        and set(candidate["oos_full_run"])
        == {
            "analyses",
            "lookahead_certificate_hash",
            "robustness",
            "robustness_proof",
            "run_manifest",
            "walk_forward",
        }
        and set(candidate["oos_full_run"]["run_manifest"])
        == {
            "config_hash",
            "manifest_hash",
            "robustness_plan_hash",
            "robustness_result_hash",
        }
        and candidate["oos_full_run"]["run_manifest"]["config_hash"]
        == candidate["oos_full_run"]["robustness"]["config_hash"]
        and candidate["oos_full_run"]["run_manifest"]["robustness_plan_hash"]
        == candidate["oos_full_run"]["robustness"]["plan_hash"]
        and candidate["oos_full_run"]["run_manifest"]["robustness_result_hash"]
        == candidate["oos_full_run"]["robustness"]["result_hash"]
        for candidate in analysis_candidates
        if candidate["oos_full_run"] is not None
    )
    oos_reference = references[GovernedResearchArtifactKind.OOS_EVIDENCE]
    exposure_reference = references[GovernedResearchArtifactKind.EXPOSURES]
    weight_reference = references[GovernedResearchArtifactKind.WEIGHTS]
    original_exposures = store.load_artifact_value(exposure_reference.snapshot_hash)
    original_weights = store.load_artifact_value(weight_reference.snapshot_hash)
    original_analyses = store.load_artifact_value(analysis_reference.snapshot_hash)
    original_selection = store.load_artifact_value(selection_reference.snapshot_hash)
    original_oos = store.load_artifact_value(oos_reference.snapshot_hash)
    assert type(original_exposures) is DerivedArtifact
    assert type(original_weights) is DerivedArtifact
    assert type(original_analyses) is DerivedArtifact
    assert type(original_selection) is DerivedArtifact
    assert type(original_oos) is DerivedArtifact

    def _content_copy(snapshot_hash: str) -> dict[str, object]:
        payload = json.loads(store.read_payload(snapshot_hash))
        assert isinstance(payload, dict)
        content = payload["content"]
        assert isinstance(content, dict)
        return json.loads(json.dumps(content))

    def _publish_evidence(
        *,
        kind: GovernedResearchArtifactKind,
        content: Mapping[str, object],
        upstream: tuple[DerivedArtifact, ...],
    ):
        return service._bundle_store._publish_prepared(
            service._bundle_store._prepare_evidence(
                kind=kind,
                bundle=bundle,
                bundle_artifact=loaded.artifact,
                bundle_source=loaded.stored.source,
                payload=content,
                upstream=upstream,
            )
        )

    def _reference(
        *,
        kind: GovernedResearchArtifactKind,
        artifact: PublishedLocalFactorMiningArtifact,
    ) -> GovernedResearchArtifactReference:
        assert artifact.stored.lineage_snapshot_hash is not None
        return GovernedResearchArtifactReference(
            kind=kind,
            snapshot_hash=artifact.stored.snapshot.snapshot_hash,
            content_hash=artifact.stored.snapshot.content_hash,
            lineage_snapshot_hash=artifact.stored.lineage_snapshot_hash,
        )

    def _assert_report_robustness_rejected(
        *,
        report_content: Mapping[str, object],
        match: str,
    ) -> None:
        alternate_report = _publish_evidence(
            kind=GovernedResearchArtifactKind.REPORT,
            content=report_content,
            upstream=(original_selection, original_oos),
        )
        alternate_reference = _reference(
            kind=GovernedResearchArtifactKind.REPORT,
            artifact=alternate_report,
        )
        alternate_manifest = replace(
            first.manifest,
            artifacts=tuple(
                alternate_reference
                if item.kind is GovernedResearchArtifactKind.REPORT
                else item
                for item in first.manifest.artifacts
            ),
            result_hash="",
            manifest_hash="",
        )
        alternate_manifest_artifact = service._bundle_store._publish_prepared(
            service._bundle_store._prepare_manifest(
                bundle=bundle,
                bundle_artifact=loaded.artifact,
                bundle_source=loaded.stored.source,
                manifest=alternate_manifest,
                report_artifact=alternate_report.artifact,
            )
        )
        with pytest.raises(LocalFactorMiningArtifactBundleError, match=match):
            service._bundle_store.load_manifest(
                alternate_manifest_artifact.stored.snapshot.snapshot_hash
            )

    missing_robustness_row = _content_copy(report_reference.snapshot_hash)
    missing_robustness_row["robustness"] = []
    _assert_report_robustness_rejected(
        report_content=missing_robustness_row,
        match="does not exactly bind frozen OOS analyses",
    )
    spurious_robustness_row = _content_copy(report_reference.snapshot_hash)
    spurious_rows = spurious_robustness_row["robustness"]
    assert isinstance(spurious_rows, list)
    assert spurious_rows
    assert isinstance(spurious_rows[0], dict)
    spurious_rows.append(
        {
            **spurious_rows[0],
            "candidate_id": "spurious_candidate",
        }
    )
    _assert_report_robustness_rejected(
        report_content=spurious_robustness_row,
        match="does not exactly bind frozen OOS analyses",
    )
    mismatched_robustness_row = _content_copy(report_reference.snapshot_hash)
    mismatched_rows = mismatched_robustness_row["robustness"]
    assert isinstance(mismatched_rows, list)
    assert mismatched_rows and isinstance(mismatched_rows[0], dict)
    mismatched_rows[0]["passed"] = not mismatched_rows[0]["passed"]
    _assert_report_robustness_rejected(
        report_content=mismatched_robustness_row,
        match="does not exactly bind frozen OOS analyses",
    )

    # A coherent artifact DAG must not be able to replace both presentation
    # labels with a forged conclusion.  The retained typed OOS proof is the
    # authority for these fields, rather than either mutable display mapping.
    matched_forged_analyses_content = _content_copy(analysis_reference.snapshot_hash)
    matched_forged_candidates = matched_forged_analyses_content["candidates"]
    assert isinstance(matched_forged_candidates, list)
    forged_candidate_id: str | None = None
    forged_passed: bool | None = None
    for candidate in matched_forged_candidates:
        assert isinstance(candidate, dict)
        oos_full_run = candidate["oos_full_run"]
        if oos_full_run is None:
            continue
        assert isinstance(oos_full_run, dict)
        robustness = oos_full_run["robustness"]
        assert isinstance(robustness, dict)
        assert type(robustness["passed"]) is bool
        forged_candidate_id = candidate["candidate_id"]
        assert isinstance(forged_candidate_id, str)
        forged_passed = not robustness["passed"]
        robustness["passed"] = forged_passed
        break
    assert forged_candidate_id is not None
    assert forged_passed is not None
    matched_forged_analyses = _publish_evidence(
        kind=GovernedResearchArtifactKind.ANALYSES,
        content=matched_forged_analyses_content,
        upstream=(original_exposures, original_weights),
    )
    matched_forged_discovery_content = _content_copy(discovery_reference.snapshot_hash)
    matched_forged_discovery_content["non_decision_evidence"] = (
        "matched_robustness_presentation_forgery"
    )
    matched_forged_discovery = _publish_evidence(
        kind=GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,
        content=matched_forged_discovery_content,
        upstream=(matched_forged_analyses.artifact,),
    )
    matched_forged_selection_content = _content_copy(selection_reference.snapshot_hash)
    matched_forged_selection_content["non_decision_evidence"] = (
        "matched_robustness_presentation_forgery"
    )
    matched_forged_selection = _publish_evidence(
        kind=GovernedResearchArtifactKind.SELECTION_EVIDENCE,
        content=matched_forged_selection_content,
        upstream=(matched_forged_discovery.artifact,),
    )
    matched_forged_oos_content = _content_copy(oos_reference.snapshot_hash)
    matched_forged_oos_content["non_decision_evidence"] = (
        "matched_robustness_presentation_forgery"
    )
    matched_forged_oos = _publish_evidence(
        kind=GovernedResearchArtifactKind.OOS_EVIDENCE,
        content=matched_forged_oos_content,
        upstream=(matched_forged_selection.artifact,),
    )
    matched_forged_report_content = _content_copy(report_reference.snapshot_hash)
    matched_forged_report_content["non_decision_evidence"] = (
        "matched_robustness_presentation_forgery"
    )
    matched_forged_report_rows = matched_forged_report_content["robustness"]
    assert isinstance(matched_forged_report_rows, list)
    for row in matched_forged_report_rows:
        assert isinstance(row, dict)
        if row["candidate_id"] == forged_candidate_id:
            row["passed"] = forged_passed
            break
    else:  # pragma: no cover - original report/assertion above guarantees this row.
        raise AssertionError("selected OOS candidate is missing from report robustness")
    matched_forged_report = _publish_evidence(
        kind=GovernedResearchArtifactKind.REPORT,
        content=matched_forged_report_content,
        upstream=(matched_forged_selection.artifact, matched_forged_oos.artifact),
    )
    matched_forged_references = tuple(
        sorted(
            (
                exposure_reference,
                weight_reference,
                _reference(
                    kind=GovernedResearchArtifactKind.ANALYSES,
                    artifact=matched_forged_analyses,
                ),
                _reference(
                    kind=GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,
                    artifact=matched_forged_discovery,
                ),
                _reference(
                    kind=GovernedResearchArtifactKind.SELECTION_EVIDENCE,
                    artifact=matched_forged_selection,
                ),
                _reference(
                    kind=GovernedResearchArtifactKind.OOS_EVIDENCE,
                    artifact=matched_forged_oos,
                ),
                _reference(
                    kind=GovernedResearchArtifactKind.REPORT,
                    artifact=matched_forged_report,
                ),
            ),
            key=lambda item: item.kind.value,
        )
    )
    matched_forged_manifest = replace(
        first.manifest,
        artifacts=matched_forged_references,
        result_hash="",
        manifest_hash="",
    )
    matched_forged_manifest_artifact = service._bundle_store._publish_prepared(
        service._bundle_store._prepare_manifest(
            bundle=bundle,
            bundle_artifact=loaded.artifact,
            bundle_source=loaded.stored.source,
            manifest=matched_forged_manifest,
            report_artifact=matched_forged_report.artifact,
        )
    )
    with pytest.raises(
        LocalFactorMiningArtifactBundleError,
        match="direct frozen OOS robustness proof",
    ):
        service._bundle_store.load_manifest(
            matched_forged_manifest_artifact.stored.snapshot.snapshot_hash
        )

    # The OOS receipt must bind the exact selected-record hash, not merely a
    # candidate ID that happens to be present in the sealed generation receipt.
    forged_selection_oos_content = _content_copy(oos_reference.snapshot_hash)
    forged_oos_release = forged_selection_oos_content["oos_release"]
    assert isinstance(forged_oos_release, dict)
    forged_oos_results = forged_oos_release["results"]
    assert isinstance(forged_oos_results, list) and forged_oos_results
    assert isinstance(forged_oos_results[0], dict)
    forged_oos_results[0]["selection_record_hash"] = sha256(
        b"forged-selection-record"
    ).hexdigest()
    forged_selection_oos = _publish_evidence(
        kind=GovernedResearchArtifactKind.OOS_EVIDENCE,
        content=forged_selection_oos_content,
        upstream=(original_selection,),
    )
    forged_selection_report_content = _content_copy(report_reference.snapshot_hash)
    forged_selection_report_content["non_decision_evidence"] = (
        "forged_selection_record"
    )
    forged_selection_report = _publish_evidence(
        kind=GovernedResearchArtifactKind.REPORT,
        content=forged_selection_report_content,
        upstream=(original_selection, forged_selection_oos.artifact),
    )
    forged_selection_references = tuple(
        sorted(
            (
                exposure_reference,
                weight_reference,
                analysis_reference,
                discovery_reference,
                selection_reference,
                _reference(
                    kind=GovernedResearchArtifactKind.OOS_EVIDENCE,
                    artifact=forged_selection_oos,
                ),
                _reference(
                    kind=GovernedResearchArtifactKind.REPORT,
                    artifact=forged_selection_report,
                ),
            ),
            key=lambda item: item.kind.value,
        )
    )
    forged_selection_manifest = replace(
        first.manifest,
        artifacts=forged_selection_references,
        result_hash="",
        manifest_hash="",
    )
    forged_selection_manifest_artifact = service._bundle_store._publish_prepared(
        service._bundle_store._prepare_manifest(
            bundle=bundle,
            bundle_artifact=loaded.artifact,
            bundle_source=loaded.stored.source,
            manifest=forged_selection_manifest,
            report_artifact=forged_selection_report.artifact,
        )
    )
    with pytest.raises(
        LocalFactorMiningArtifactBundleError,
        match="does not bind its sealed selected candidate record",
    ):
        service._bundle_store.load_manifest(
            forged_selection_manifest_artifact.stored.snapshot.snapshot_hash
        )

    no_oos_analyses = _content_copy(
        analysis_reference.snapshot_hash
    )
    no_oos_candidates = no_oos_analyses["candidates"]
    assert isinstance(no_oos_candidates, list)
    for candidate in no_oos_candidates:
        assert isinstance(candidate, dict)
        candidate["oos_full_run"] = None
    alternate_analyses = _publish_evidence(
        kind=GovernedResearchArtifactKind.ANALYSES,
        content=no_oos_analyses,
        upstream=(original_exposures, original_weights),
    )
    no_oos_discovery = _content_copy(discovery_reference.snapshot_hash)
    no_oos_discovery["non_decision_evidence"] = "no_oos_robustness_lineage"
    alternate_discovery = _publish_evidence(
        kind=GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,
        content=no_oos_discovery,
        upstream=(alternate_analyses.artifact,),
    )
    no_oos_selection = _content_copy(selection_reference.snapshot_hash)
    no_oos_selection["non_decision_evidence"] = "no_oos_robustness_lineage"
    alternate_selection = _publish_evidence(
        kind=GovernedResearchArtifactKind.SELECTION_EVIDENCE,
        content=no_oos_selection,
        upstream=(alternate_discovery.artifact,),
    )
    no_oos_report = _content_copy(report_reference.snapshot_hash)
    no_oos_report["oos_release_hash"] = None
    # Deleting the presentation rows as well cannot turn a selected commitment
    # into a legitimate no-selection/no-OOS research result.
    no_oos_report["robustness"] = []
    alternate_report = _publish_evidence(
        kind=GovernedResearchArtifactKind.REPORT,
        content=no_oos_report,
        upstream=(alternate_selection.artifact,),
    )
    alternate_references = tuple(
        sorted(
            (
                exposure_reference,
                weight_reference,
                _reference(
                    kind=GovernedResearchArtifactKind.ANALYSES,
                    artifact=alternate_analyses,
                ),
                _reference(
                    kind=GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,
                    artifact=alternate_discovery,
                ),
                _reference(
                    kind=GovernedResearchArtifactKind.SELECTION_EVIDENCE,
                    artifact=alternate_selection,
                ),
                _reference(
                    kind=GovernedResearchArtifactKind.REPORT,
                    artifact=alternate_report,
                ),
            ),
            key=lambda item: item.kind.value,
        )
    )
    no_oos_manifest = replace(
        first.manifest,
        artifacts=alternate_references,
        oos_release_hash=None,
        result_hash="",
        manifest_hash="",
    )
    no_oos_manifest_artifact = service._bundle_store._publish_prepared(
        service._bundle_store._prepare_manifest(
            bundle=bundle,
            bundle_artifact=loaded.artifact,
            bundle_source=loaded.stored.source,
            manifest=no_oos_manifest,
            report_artifact=alternate_report.artifact,
        )
    )
    with pytest.raises(
        LocalFactorMiningArtifactBundleError,
        match="does not exactly match the sealed selection commitment",
    ):
        service._bundle_store.load_manifest(
            no_oos_manifest_artifact.stored.snapshot.snapshot_hash
        )
    alternate_selection_payload = json.loads(
        store.read_payload(selection_reference.snapshot_hash)
    )
    alternate_selection_payload["content"]["non_decision_evidence"] = "spliced"
    alternate_selection = service._bundle_store._publish_prepared(
        service._bundle_store._prepare_evidence(
            kind=GovernedResearchArtifactKind.SELECTION_EVIDENCE,
            bundle=bundle,
            bundle_artifact=loaded.artifact,
            bundle_source=loaded.stored.source,
            payload=alternate_selection_payload["content"],
            upstream=(original_discovery,),
        )
    )
    assert alternate_selection.stored.lineage_snapshot_hash is not None
    alternate_selection_reference = GovernedResearchArtifactReference(
        kind=GovernedResearchArtifactKind.SELECTION_EVIDENCE,
        snapshot_hash=alternate_selection.stored.snapshot.snapshot_hash,
        content_hash=alternate_selection.stored.snapshot.content_hash,
        lineage_snapshot_hash=alternate_selection.stored.lineage_snapshot_hash,
    )
    spliced_manifest = replace(
        first.manifest,
        artifacts=tuple(
            alternate_selection_reference
            if item.kind is GovernedResearchArtifactKind.SELECTION_EVIDENCE
            else item
            for item in first.manifest.artifacts
        ),
        result_hash="",
        manifest_hash="",
    )
    spliced_manifest_artifact = service._bundle_store._publish_prepared(
        service._bundle_store._prepare_manifest(
            bundle=bundle,
            bundle_artifact=loaded.artifact,
            bundle_source=loaded.stored.source,
            manifest=spliced_manifest,
            report_artifact=original_report,
        )
    )
    before_spliced_replay = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    with pytest.raises(LocalFactorMiningResearchError, match="governed immutable artifact"):
        service.inspect(
            artifact_snapshot_hash=spliced_manifest_artifact.stored.snapshot.snapshot_hash
        )
    with pytest.raises(LocalFactorMiningResearchError, match="expected local research manifest"):
        service.replay(
            bundle_snapshot_hash=bundle_snapshot_hash,
            expected_manifest_snapshot_hash=spliced_manifest_artifact.stored.snapshot.snapshot_hash,
        )
    after_spliced_replay = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    manifest = service.inspect(artifact_snapshot_hash=first.manifest_snapshot_hash)

    assert definition == {
        "artifact_snapshot_hash": bundle_snapshot_hash,
        "bundle_hash": bundle.bundle_hash,
        "dataset_version_hashes": list(bundle.dataset_version_hashes),
        "decision_replay_plan_hash": plan.schedule_hash,
        "kind": "definition",
        "retention": bundle.config.retention_mapping(),
        "research_only": True,
    }
    assert first.research_only is True
    assert first.candidate_admission_eligible is False
    assert first.simnow_handoff_allowed is False
    assert first.manifest.manifest_hash == replayed.manifest.manifest_hash
    assert first.manifest.result_hash == replayed.manifest.result_hash
    assert first.manifest_snapshot_hash == replayed.manifest_snapshot_hash
    assert after_replay == before_replay
    assert after_tampered_replay == before_replay
    assert after_spliced_replay == before_spliced_replay
    assert {item.kind for item in first.manifest.artifacts} == {
        GovernedResearchArtifactKind.EXPOSURES,
        GovernedResearchArtifactKind.WEIGHTS,
        GovernedResearchArtifactKind.ANALYSES,
        GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,
        GovernedResearchArtifactKind.SELECTION_EVIDENCE,
        GovernedResearchArtifactKind.OOS_EVIDENCE,
        GovernedResearchArtifactKind.REPORT,
    }
    assert all(item.lineage_snapshot_hash for item in first.manifest.artifacts)
    assert all(store.load_artifact(item.snapshot_hash).lineage_snapshot_hash for item in first.manifest.artifacts)
    assert manifest == {
        "artifact_snapshot_hash": first.manifest_snapshot_hash,
        "artifact_snapshot_hashes": [item.snapshot_hash for item in first.manifest.artifacts],
        "bundle_hash": bundle.bundle_hash,
        "kind": "manifest",
        "manifest_hash": first.manifest.manifest_hash,
        "research_only": True,
        "result_hash": first.manifest.result_hash,
    }
    golden = json.loads(_LOCAL_FACTOR_MINING_RUN_BUNDLE_GOLDEN_FIXTURE.read_text(encoding="utf-8"))
    assert golden == {
        "artifact_contents": {
            item.kind.value: item.content_hash for item in first.manifest.artifacts
        },
        "artifact_snapshots": {
            item.kind.value: item.snapshot_hash for item in first.manifest.artifacts
        },
        "bundle_hash": bundle.bundle_hash,
        "bundle_snapshot_hash": bundle_snapshot_hash,
        "discovery_result_hash": first.discovery.discovery_result_hash,
        "format": "northstar.local-factor-mining-run-bundle-golden.v1",
        "manifest_hash": first.manifest.manifest_hash,
        "manifest_snapshot_hash": first.manifest_snapshot_hash,
        "oos_release_hash": first.release.release_hash if first.release is not None else None,
        "result_hash": first.manifest.result_hash,
        "selection_commitment_hash": first.commitment.commitment_hash,
    }


@pytest.mark.e2e
@pytest.mark.regression
def test_local_factor_mining_no_selection_runs_and_replays_without_oos_evidence(
    tmp_path: Path,
) -> None:
    """A fully rejected receipt is a valid no-selection local research outcome."""

    store, plan, days = _publish_checkpoint_plan(tmp_path)
    base_campaign = _factor_mining_campaign(
        config=_factor_config(days),
        plan=plan,
        days=days,
    )
    campaign = replace(
        base_campaign,
        selection_policy=replace(
            base_campaign.selection_policy,
            # This is a legal host-owned threshold, deliberately far above the
            # bounded synthetic daily-return fixture.  It forces no selection
            # after real discovery evidence rather than by using invalid input.
            minimum_validation_total_return=100.0,
        ),
    )
    generated = _factor_mining_receipt(campaign)
    receipt = replace(
        generated,
        proposals=(generated.proposals[0],),
    )
    bundle = LocalFactorMiningRunBundle(
        dataset_version_hashes=campaign.dataset_version_hashes,
        plan=plan,
        campaign=campaign,
        generation=receipt,
        config=LocalFactorMiningRunConfig(
            config_id="local_factor_no_selection",
            code_revision=campaign.template.code_revision,
            code_revision_hash=_LOCAL_FACTOR_CODE_REVISION_HASH,
            output_schema_version="local_factor_research_v1",
            output_transform_version="local_factor_research_v1",
            retention_days=3650,
            automatic_cleanup=False,
        ),
    )
    service = LocalFactorMiningResearchService(artifact_store=store)
    bundle_snapshot_hash = service.publish_definition(bundle=bundle)

    first = service.run(bundle_snapshot_hash=bundle_snapshot_hash)
    assert first.release is None
    assert not first.commitment.selected_records
    assert len(first.discovery.results) == 1
    assert (
        first.discovery.results[0].disposition
        is CandidateDiscoveryDisposition.DISCOVERY_EVALUATED
    )
    assert GovernedResearchArtifactKind.OOS_EVIDENCE not in {
        item.kind for item in first.manifest.artifacts
    }
    references = {item.kind: item for item in first.manifest.artifacts}
    report_payload = json.loads(
        store.read_payload(references[GovernedResearchArtifactKind.REPORT].snapshot_hash)
    )
    assert report_payload["content"]["robustness"] == []
    assert report_payload["content"]["oos_release_hash"] is None

    before_replay = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    replayed = service.replay(
        bundle_snapshot_hash=bundle_snapshot_hash,
        expected_manifest_snapshot_hash=first.manifest_snapshot_hash,
    )
    after_replay = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    assert replayed.release is None
    assert replayed.manifest == first.manifest
    assert replayed.manifest_snapshot_hash == first.manifest_snapshot_hash
    assert after_replay == before_replay


@pytest.mark.e2e
@pytest.mark.failure
def test_local_factor_mining_bundle_rejects_plan_mismatch_and_latest_wire_text(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    config = LocalFactorMiningRunConfig(
        config_id="local_factor_evidence",
        code_revision=campaign.template.code_revision,
        code_revision_hash=_LOCAL_FACTOR_CODE_REVISION_HASH,
        output_schema_version="local_factor_research_v1",
        output_transform_version="local_factor_research_v1",
        retention_days=3650,
        automatic_cleanup=False,
    )
    with pytest.raises(LocalFactorMiningRunBundleError, match="code_revision_hash"):
        replace(config, code_revision_hash="untrusted-moving-label", config_hash="")
    with pytest.raises(LocalFactorMiningRunBundleError, match="does not bind the replay plan"):
        LocalFactorMiningRunBundle(
            dataset_version_hashes=campaign.dataset_version_hashes,
            plan=plan,
            campaign=replace(
                campaign,
                decision_replay_plan_hash=sha256(b"other-plan").hexdigest(),
            ),
            generation=_factor_mining_receipt(campaign),
            config=config,
        )

    bundle = LocalFactorMiningRunBundle(
        dataset_version_hashes=campaign.dataset_version_hashes,
        plan=plan,
        campaign=campaign,
        generation=_factor_mining_receipt(campaign),
        config=config,
    )
    payload = json.loads(bundle.to_bytes())
    payload["bundle"]["fields"]["config"]["fields"]["config_hash"] = False
    malformed_hash_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(LocalFactorMiningRunBundleError, match="cannot be reconstructed"):
        LocalFactorMiningRunBundle.from_bytes(malformed_hash_payload)

    payload = json.loads(bundle.to_bytes())
    assert _replace_first_utc_datetime_with_z(payload) is True
    noncanonical_datetime_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(LocalFactorMiningRunBundleError, match="canonical typed declaration"):
        LocalFactorMiningRunBundle.from_bytes(noncanonical_datetime_payload)

    payload = json.loads(bundle.to_bytes())
    payload["bundle"]["fields"]["config"]["fields"]["code_revision"] = "latest"
    unsafe_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(LocalFactorMiningRunBundleError, match="latest selectors"):
        LocalFactorMiningRunBundle.from_bytes(unsafe_payload)

    with pytest.raises(LocalFactorMiningResearchError, match="verified bundle artifact"):
        LocalFactorMiningResearchService(artifact_store=store).run(
            bundle_snapshot_hash=plan.checkpoints[0].dataset_version_hash
        )


@pytest.mark.e2e
@pytest.mark.failure
def test_local_factor_mining_replay_rejects_a_manifest_for_another_bundle_before_writing(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    config = LocalFactorMiningRunConfig(
        config_id="local_factor_evidence",
        code_revision=campaign.template.code_revision,
        code_revision_hash=_LOCAL_FACTOR_CODE_REVISION_HASH,
        output_schema_version="local_factor_research_v1",
        output_transform_version="local_factor_research_v1",
        retention_days=3650,
        automatic_cleanup=False,
    )
    bundle = LocalFactorMiningRunBundle(
        dataset_version_hashes=campaign.dataset_version_hashes,
        plan=plan,
        campaign=campaign,
        generation=_factor_mining_receipt(campaign),
        config=config,
    )
    other_bundle = replace(
        bundle,
        config=replace(config, config_id="other_local_factor_evidence", config_hash=""),
        bundle_hash="",
    )
    service = LocalFactorMiningResearchService(artifact_store=store)
    bundle_snapshot_hash = service.publish_definition(bundle=bundle)
    other_bundle_snapshot_hash = service.publish_definition(bundle=other_bundle)
    first = service.run(bundle_snapshot_hash=bundle_snapshot_hash)
    before = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )

    with pytest.raises(LocalFactorMiningResearchError, match="does not bind the supplied"):
        service.replay(
            bundle_snapshot_hash=other_bundle_snapshot_hash,
            expected_manifest_snapshot_hash=first.manifest_snapshot_hash,
        )

    after = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    assert after == before


@pytest.mark.e2e
@pytest.mark.failure
def test_local_factor_mining_rejects_semantic_artifact_spoofs_before_replay_writes(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    bundle = LocalFactorMiningRunBundle(
        dataset_version_hashes=campaign.dataset_version_hashes,
        plan=plan,
        campaign=campaign,
        generation=_factor_mining_receipt(campaign),
        config=LocalFactorMiningRunConfig(
            config_id="local_factor_evidence",
            code_revision=campaign.template.code_revision,
            code_revision_hash=_LOCAL_FACTOR_CODE_REVISION_HASH,
            output_schema_version="local_factor_research_v1",
            output_transform_version="local_factor_research_v1",
            retention_days=3650,
            automatic_cleanup=False,
        ),
    )
    service = LocalFactorMiningResearchService(artifact_store=store)
    bundle_snapshot_hash = service.publish_definition(bundle=bundle)
    loaded = service._bundle_store.load_definition(bundle_snapshot_hash)
    common = {
        "campaign_hash": bundle.campaign.campaign_hash,
        "config_hash": bundle.config.config_hash,
        "dataset_version_hashes": list(bundle.dataset_version_hashes),
        "decision_replay_plan_hash": bundle.plan.schedule_hash,
        "generation_receipt_hash": bundle.generation.receipt_hash,
    }
    spoofed_evidence = service._bundle_store._publish_prepared(
        service._bundle_store._prepare(
            artifact_key="exposures",
            payload=json.dumps(
                {
                    "content": common,
                    "format": "northstar.local-factor-mining-artifact.v1",
                    "kind": "exposures",
                    "research_only": True,
                    "retention": bundle.config.retention_mapping(),
                    "run_bundle_hash": sha256(b"other-bundle").hexdigest(),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            bundle=bundle,
            config=bundle.config,
            parents=(loaded.artifact,),
            source=loaded.stored.source,
        )
    )
    type_spoofed_evidence = service._bundle_store._publish_prepared(
        service._bundle_store._prepare(
            artifact_key="exposures",
            payload=json.dumps(
                {
                    "content": common,
                    "format": "northstar.local-factor-mining-artifact.v1",
                    "kind": "exposures",
                    "research_only": True,
                    "retention": {
                        "automatic_cleanup": 0,
                        "policy_id": bundle.config.config_id,
                        "retention_days": float(bundle.config.retention_days),
                    },
                    "run_bundle_hash": bundle.bundle_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            bundle=bundle,
            config=bundle.config,
            parents=(loaded.artifact,),
            source=loaded.stored.source,
        )
    )
    fake_references = tuple(
        sorted(
            (
                GovernedResearchArtifactReference(
                    kind=kind,
                    snapshot_hash=sha256(f"fake-{kind.value}-snapshot".encode()).hexdigest(),
                    content_hash=sha256(f"fake-{kind.value}-content".encode()).hexdigest(),
                    lineage_snapshot_hash=sha256(
                        f"fake-{kind.value}-lineage".encode()
                    ).hexdigest(),
                )
                for kind in (
                    GovernedResearchArtifactKind.EXPOSURES,
                    GovernedResearchArtifactKind.WEIGHTS,
                    GovernedResearchArtifactKind.ANALYSES,
                    GovernedResearchArtifactKind.DISCOVERY_EVIDENCE,
                    GovernedResearchArtifactKind.SELECTION_EVIDENCE,
                    GovernedResearchArtifactKind.REPORT,
                )
            ),
            key=lambda item: item.kind.value,
        )
    )
    fake_manifest = LocalFactorMiningRunManifest(
        bundle_hash=bundle.bundle_hash,
        dataset_version_hashes=bundle.dataset_version_hashes,
        decision_replay_plan_hash=bundle.plan.schedule_hash,
        campaign_hash=bundle.campaign.campaign_hash,
        generation_receipt_hash=bundle.generation.receipt_hash,
        discovery_result_hash=sha256(b"fake-discovery").hexdigest(),
        selection_commitment_hash=sha256(b"fake-selection").hexdigest(),
        oos_release_hash=None,
        config_hash=bundle.config.config_hash,
        artifacts=fake_references,
    )
    with pytest.raises(LocalFactorMiningRunBundleError, match="oos_release_hash"):
        replace(
            fake_manifest,
            oos_release_hash=False,
            result_hash="",
            manifest_hash="",
        )
    manifest_payload = json.loads(fake_manifest.to_bytes())
    manifest_payload["manifest"]["fields"]["result_hash"] = False
    with pytest.raises(LocalFactorMiningRunBundleError, match="cannot be reconstructed"):
        LocalFactorMiningRunManifest.from_bytes(
            json.dumps(
                manifest_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    spoofed_manifest = service._bundle_store._publish_prepared(
        service._bundle_store._prepare(
            artifact_key="manifest",
            payload=fake_manifest.to_bytes(),
            bundle=bundle,
            config=bundle.config,
            parents=(loaded.artifact,),
            source=loaded.stored.source,
        )
    )

    with pytest.raises(LocalFactorMiningResearchError, match="governed immutable artifact"):
        service.inspect(artifact_snapshot_hash=spoofed_evidence.stored.snapshot.snapshot_hash)
    with pytest.raises(LocalFactorMiningResearchError, match="governed immutable artifact"):
        service.inspect(
            artifact_snapshot_hash=type_spoofed_evidence.stored.snapshot.snapshot_hash
        )
    with pytest.raises(LocalFactorMiningResearchError, match="governed immutable artifact"):
        service.inspect(artifact_snapshot_hash=spoofed_manifest.stored.snapshot.snapshot_hash)

    before = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    with pytest.raises(LocalFactorMiningResearchError, match="expected local research manifest"):
        service.replay(
            bundle_snapshot_hash=bundle_snapshot_hash,
            expected_manifest_snapshot_hash=spoofed_manifest.stored.snapshot.snapshot_hash,
        )
    after = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    )
    assert after == before


@pytest.mark.e2e
@pytest.mark.statistical
def test_oos_price_perturbation_cannot_change_development_selection_ranking(
    tmp_path: Path,
) -> None:
    """Changing only post-selection OOS prices cannot affect discovery policy output."""

    base_store, base_plan, days = _publish_checkpoint_plan(tmp_path / "base")
    perturbed_store, perturbed_plan, _ = _publish_checkpoint_plan(
        tmp_path / "perturbed",
        return_overrides={
            (symbol, return_index): (-0.40 if symbol in {"AL_CONT", "RB_CONT"} else 0.35)
            for symbol in _SYMBOLS
            for return_index in range(10, 14)
        },
    )
    base_campaign = _factor_mining_campaign(
        config=_factor_config(days),
        plan=base_plan,
        days=days,
    )
    perturbed_campaign = _factor_mining_campaign(
        config=_factor_config(days),
        plan=perturbed_plan,
        days=days,
    )
    base_runner = FactorMiningCampaignRunner(
        artifact_store=base_store,
        campaign=base_campaign,
        plan=base_plan,
    )
    perturbed_runner = FactorMiningCampaignRunner(
        artifact_store=perturbed_store,
        campaign=perturbed_campaign,
        plan=perturbed_plan,
    )
    base_discovery = base_runner.evaluate_discovery_candidate_batch(
        request=EvaluateFactorCandidateDiscoveryBatchRequest(
            generation=_factor_mining_receipt(base_campaign)
        )
    )
    perturbed_discovery = perturbed_runner.evaluate_discovery_candidate_batch(
        request=EvaluateFactorCandidateDiscoveryBatchRequest(
            generation=_factor_mining_receipt(perturbed_campaign)
        )
    )
    base_commitment = base_runner.commit_selection(discovery=base_discovery)
    perturbed_commitment = perturbed_runner.commit_selection(discovery=perturbed_discovery)

    assert base_campaign.campaign_hash != perturbed_campaign.campaign_hash
    assert tuple(
        (
            record.candidate_id,
            record.disposition,
            record.reason_code,
            record.rank,
            record.discovery_score,
        )
        for record in base_commitment.records
    ) == tuple(
        (
            record.candidate_id,
            record.disposition,
            record.reason_code,
            record.rank,
            record.discovery_score,
        )
        for record in perturbed_commitment.records
    )


@pytest.mark.e2e
@pytest.mark.failure
def test_factor_mining_campaign_rejects_selection_at_the_first_oos_decision(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    unsafe_campaign = replace(campaign, selection_at=plan.checkpoints[11].decision_at)

    with pytest.raises(FactorMiningCampaignError, match="strictly before the earliest OOS"):
        FactorMiningCampaignRunner(
            artifact_store=store,
            campaign=unsafe_campaign,
            plan=plan,
        )


@pytest.mark.e2e
@pytest.mark.failure
def test_factor_mining_campaign_rejects_selection_before_validation_outcomes_mature(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    unsafe_campaign = replace(campaign, selection_at=plan.checkpoints[9].decision_at)

    with pytest.raises(FactorMiningCampaignError, match="strictly after all discovery outcome"):
        FactorMiningCampaignRunner(
            artifact_store=store,
            campaign=unsafe_campaign,
            plan=plan,
        )


@pytest.mark.e2e
@pytest.mark.failure
def test_factor_mining_campaign_rejects_walk_forward_layout_that_reuses_oos_as_development(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    unsafe_template = replace(
        campaign.template,
        walk_forward_folds=_factor_config(days).walk_forward_folds,
    )
    with pytest.raises(FactorMiningError, match="shared in-sample and validation"):
        replace(campaign, template=unsafe_template)


@pytest.mark.e2e
@pytest.mark.failure
def test_factor_mining_campaign_preflights_canonical_features_before_evaluation(
    tmp_path: Path,
) -> None:
    store, plan, days = _publish_checkpoint_plan(tmp_path)
    campaign = _factor_mining_campaign(config=_factor_config(days), plan=plan, days=days)
    unknown_primitive = FactorPrimitive(
        primitive_id="unknown_daily_feature",
        feature_id="unknown.daily_feature",
        allowed_directions=(-1.0, 1.0),
        parameter_domains=(
            FactorParameterDomain(name="lookback_bars", allowed_values=(1, 2, 3)),
        ),
    )
    unsafe_campaign = replace(campaign, primitives=(unknown_primitive,))

    with pytest.raises(FactorMiningCampaignError, match="known canonical feature"):
        FactorMiningCampaignRunner(
            artifact_store=store,
            campaign=unsafe_campaign,
            plan=plan,
        )


@pytest.mark.e2e
@pytest.mark.failure
def test_factor_research_pipeline_rejects_future_dataset_version_at_earlier_checkpoint(
    tmp_path: Path,
) -> None:
    days = _days()
    store = ArtifactStore(tmp_path / "artifacts")
    _, future_dataset = publish_authorized_pit_dataset(
        tmp_path,
        _feature_bar_frame(days),
        dataset_id="factor_research_future_feature_bar",
        source_id="factor_research_future_fixture_source",
        adapter_id="factor-research-future-fixture-adapter",
        schema_version="cn_futures_feature_bar_v1",
        artifact_id="factor-research-future-version",
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close", "volume"),
        normalized_available_at=_decision_at(days[-1]) - timedelta(minutes=1),
        store=store,
        scope_exchanges=("SHFE",),
        scope_products=("AL", "CU", "RB", "ZN"),
    )
    unsafe_checkpoint = DecisionReplayCheckpoint(
        decision_at=_decision_at(days[3]),
        decision_event_time=days[3],
        dataset_version_hash=future_dataset.version_hash,
        pit_spec=_pit_spec(),
    )

    with pytest.raises(MarketDataPITError, match="尚不可用"):
        FactorResearchPipeline(artifact_store=store, config=_factor_config(days)).run(
            plan=DecisionReplayPlan.create((unsafe_checkpoint,))
        )
