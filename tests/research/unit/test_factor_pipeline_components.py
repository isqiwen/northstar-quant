"""Deterministic unit coverage for the research-only factor pipeline components."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
import hashlib
import math

import polars as pl
import pytest

from northstar_quant.research.factors.analysis import analyze_factor
from northstar_quant.research.factors.models import (
    FactorCheckpointData,
    FactorDefinition,
    FactorExposure,
    FactorForwardOutcome,
    FactorMarketSlice,
    FactorMaterializationReference,
    FactorPipelineConfig,
    FactorPortfolioProposal,
    FactorResearchError,
    FactorResearchExperiment,
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
    ProposalStatus,
)
from northstar_quant.research.factors.portfolio import build_factor_portfolio_proposal
from northstar_quant.research.factors.frames import build_factor_target_frame
from northstar_quant.research.validation.framework import (
    ValidationPeriod,
    ValidationSplit,
    WalkForwardFold,
)


SESSION_1 = date(2026, 1, 5)
SESSION_2 = date(2026, 1, 6)
SYMBOLS = ("AL_CONT", "CU_CONT", "RB_CONT", "ZN_CONT")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decision_at(session: date) -> datetime:
    return datetime(session.year, session.month, session.day, 16, tzinfo=UTC)


def _analysis_exposures(
    checkpoint_id: str,
    session: date,
    values: Mapping[str, float],
) -> tuple[FactorExposure, ...]:
    checkpoint_hash = _hash(f"analysis-checkpoint:{checkpoint_id}")
    return tuple(
        FactorExposure(
            checkpoint_hash=checkpoint_hash,
            decision_at=_decision_at(session),
            decision_session=session,
            snapshot_id=_hash(f"analysis-snapshot:{checkpoint_id}"),
            factor_id="momentum",
            factor_definition_hash=_hash("analysis-momentum-definition"),
            config_hash=_hash("analysis-config"),
            materialization_hash=_hash(f"analysis-materialization:{checkpoint_id}"),
            symbol=symbol,
            value=value,
        )
        for symbol, value in values.items()
    )


def _analysis_outcomes(
    checkpoint_id: str,
    session: date,
    values: Mapping[str, float],
) -> tuple[FactorForwardOutcome, ...]:
    evaluation_session = session + timedelta(days=1)
    return tuple(
        FactorForwardOutcome(
            origin_checkpoint_hash=_hash(f"analysis-checkpoint:{checkpoint_id}"),
            decision_session=session,
            evaluation_checkpoint_hash=_hash(f"analysis-evaluation:{checkpoint_id}"),
            evaluation_session=evaluation_session,
            evaluation_at=_decision_at(evaluation_session),
            symbol=symbol,
            forward_return=value,
        )
        for symbol, value in values.items()
    )


def _folds() -> tuple[WalkForwardFold, WalkForwardFold]:
    return (
        WalkForwardFold(
            fold_id="fold_1",
            split=ValidationSplit(
                in_sample=ValidationPeriod(date(2025, 1, 2), date(2025, 1, 2)),
                validation=ValidationPeriod(date(2025, 1, 3), date(2025, 1, 3)),
                out_of_sample=ValidationPeriod(date(2025, 1, 4), date(2025, 1, 4)),
            ),
        ),
        WalkForwardFold(
            fold_id="fold_2",
            split=ValidationSplit(
                in_sample=ValidationPeriod(date(2025, 1, 5), date(2025, 1, 5)),
                validation=ValidationPeriod(date(2025, 1, 6), date(2025, 1, 6)),
                out_of_sample=ValidationPeriod(date(2025, 1, 7), date(2025, 1, 7)),
            ),
        ),
    )


def _robustness_plan() -> FactorRobustnessPlan:
    return FactorRobustnessPlan(
        plan_id="component_robustness",
        version="v1",
        subperiods=(
            FactorRobustnessSubperiod(
                scenario_id="early",
                period=ValidationPeriod(date(2025, 1, 4), date(2025, 1, 4)),
                excluded_symbols=(),
            ),
            FactorRobustnessSubperiod(
                scenario_id="late_exclude_al",
                period=ValidationPeriod(date(2025, 1, 7), date(2025, 1, 7)),
                excluded_symbols=("AL_CONT",),
            ),
        ),
        parameter_variants=(
            FactorRobustnessParameterVariant.create(
                variant_id="alpha_one_lookback_10",
                factor_id="alpha_one",
                parameters={"lookback": 10},
            ),
            FactorRobustnessParameterVariant.create(
                variant_id="alpha_one_lookback_20",
                factor_id="alpha_one",
                parameters={"lookback": 20},
            ),
            FactorRobustnessParameterVariant.create(
                variant_id="alpha_two_lookback_10",
                factor_id="alpha_two",
                parameters={"lookback": 10},
            ),
            FactorRobustnessParameterVariant.create(
                variant_id="alpha_two_lookback_5",
                factor_id="alpha_two",
                parameters={"lookback": 5},
            ),
        ),
        cost_scenarios=(
            FactorRobustnessCostScenario(
                scenario_id="adverse",
                commission_bps=2.0,
                min_commission=0.0,
                slippage_bps=4.0,
                execution_delay_sessions=1,
            ),
            FactorRobustnessCostScenario(
                scenario_id="baseline",
                commission_bps=1.0,
                min_commission=0.0,
                slippage_bps=2.0,
                execution_delay_sessions=1,
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


def _pipeline_config(
    *,
    alpha_one_budget: float = 0.75,
    target_volatility: float = 0.02,
    max_abs_weight: float = 1.0,
    max_gross_exposure: float = 1.0,
) -> FactorPipelineConfig:
    alpha_two_budget = 1.0 - alpha_one_budget
    factors = (
        FactorDefinition.create(
            factor_id="alpha_one",
            feature_id="technical.momentum",
            role=FactorRole.ALPHA,
            direction=1.0,
            risk_budget=alpha_one_budget,
            parameters={"lookback": 20},
        ),
        FactorDefinition.create(
            factor_id="alpha_two",
            feature_id="technical.reversal",
            role=FactorRole.ALPHA,
            direction=-1.0,
            risk_budget=alpha_two_budget,
            parameters={"lookback": 10},
        ),
        FactorDefinition.create(
            factor_id="volatility",
            feature_id="technical.realized_volatility",
            role=FactorRole.RISK_MODEL,
            direction=1.0,
            risk_budget=0.0,
            parameters={"lookback": 20},
        ),
    )
    return FactorPipelineConfig(
        pipeline_id="factor_research",
        version="v1",
        feature_version="v1",
        code_revision="factor-component-test",
        factors=factors,
        volatility_factor_id="volatility",
        min_cross_section=4,
        quantile_count=2,
        target_volatility=target_volatility,
        max_abs_weight=max_abs_weight,
        max_gross_exposure=max_gross_exposure,
        holding_period_sessions=1,
        initial_cash=1_000_000.0,
        commission_bps=1.0,
        min_commission=0.0,
        slippage_bps=2.0,
        execution_delay_sessions=1,
        walk_forward_folds=_folds(),
        robustness_plan=_robustness_plan(),
    )


def _checkpoint_data(
    config: FactorPipelineConfig,
    *,
    checkpoint_id: str,
    alpha_one: Mapping[str, float],
    alpha_two: Mapping[str, float],
    volatility: Mapping[str, float],
    market_symbols: tuple[str, ...] = SYMBOLS,
    session: date = SESSION_1,
) -> FactorCheckpointData:
    checkpoint_hash = _hash(f"portfolio-checkpoint:{checkpoint_id}")
    snapshot_id = _hash(f"portfolio-snapshot:{checkpoint_id}")
    factor_values = {
        "alpha_one": alpha_one,
        "alpha_two": alpha_two,
        "volatility": volatility,
    }
    materializations = tuple(
        FactorMaterializationReference(
            factor_id=definition.factor_id,
            factor_definition_hash=definition.definition_hash,
            feature_version_hash=_hash(f"feature-version:{definition.factor_id}"),
            materialization_hash=_hash(f"materialization:{checkpoint_id}:{definition.factor_id}"),
        )
        for definition in config.factors
    )
    exposures = tuple(
        FactorExposure(
            checkpoint_hash=checkpoint_hash,
            decision_at=_decision_at(session),
            decision_session=session,
            snapshot_id=snapshot_id,
            factor_id=definition.factor_id,
            factor_definition_hash=definition.definition_hash,
            config_hash=config.config_hash,
            materialization_hash=_hash(f"materialization:{checkpoint_id}:{definition.factor_id}"),
            symbol=symbol,
            value=value,
        )
        for definition in config.factors
        for symbol, value in factor_values[definition.factor_id].items()
    )
    market_slices = tuple(
        FactorMarketSlice(
            checkpoint_hash=checkpoint_hash,
            decision_session=session,
            snapshot_id=snapshot_id,
            symbol=symbol,
            close=100.0 + index,
        )
        for index, symbol in enumerate(market_symbols)
    )
    return FactorCheckpointData(
        checkpoint_hash=checkpoint_hash,
        decision_at=_decision_at(session),
        decision_session=session,
        market_evidence_hash=_hash(f"market-evidence:{checkpoint_id}"),
        snapshot_id=snapshot_id,
        dataset_version_hash=_hash(f"dataset-version:{checkpoint_id}"),
        config_hash=config.config_hash,
        materializations=materializations,
        exposures=exposures,
        market_slices=market_slices,
    )


def _portfolio_inputs() -> dict[str, dict[str, float]]:
    return {
        "alpha_one": {
            "AL_CONT": 1.0,
            "CU_CONT": 2.0,
            "RB_CONT": 3.0,
            "ZN_CONT": 4.0,
        },
        "alpha_two": {
            "AL_CONT": 1.0,
            "CU_CONT": 2.0,
            "RB_CONT": 3.0,
            "ZN_CONT": 4.0,
        },
        "volatility": {
            "AL_CONT": 0.1,
            "CU_CONT": 0.2,
            "RB_CONT": 0.4,
            "ZN_CONT": 0.8,
        },
    }


def test_analysis_is_deterministic_for_ic_rank_ic_quantiles_and_turnover() -> None:
    first_exposures = _analysis_exposures(
        "first",
        SESSION_1,
        {"AL_CONT": 1.0, "CU_CONT": 2.0, "RB_CONT": 3.0, "ZN_CONT": 4.0},
    )
    second_exposures = _analysis_exposures(
        "second",
        SESSION_2,
        {"AL_CONT": 4.0, "CU_CONT": 3.0, "RB_CONT": 2.0, "ZN_CONT": 1.0},
    )
    first_outcomes = _analysis_outcomes(
        "first",
        SESSION_1,
        {"AL_CONT": 0.01, "CU_CONT": 0.02, "RB_CONT": 0.03, "ZN_CONT": 0.04},
    )
    second_outcomes = _analysis_outcomes(
        "second",
        SESSION_2,
        {"AL_CONT": 0.01, "CU_CONT": 0.02, "RB_CONT": 0.03, "ZN_CONT": 0.04},
    )

    result = analyze_factor(
        factor_id="momentum",
        exposures=tuple(reversed(first_exposures + second_exposures)),
        outcomes=tuple(reversed(first_outcomes + second_outcomes)),
        quantile_count=2,
        min_cross_section=4,
    )

    first, second = result.periods
    assert (first.decision_session, second.decision_session) == (SESSION_1, SESSION_2)
    assert (first.ic, first.rank_ic) == pytest.approx((1.0, 1.0))
    assert (second.ic, second.rank_ic) == pytest.approx((-1.0, -1.0))
    assert tuple(bucket for bucket, _ in first.quantile_returns) == (1, 2)
    assert tuple(value for _, value in first.quantile_returns) == pytest.approx((0.015, 0.035))
    assert tuple(bucket for bucket, _ in second.quantile_returns) == (1, 2)
    assert tuple(value for _, value in second.quantile_returns) == pytest.approx((0.035, 0.015))
    assert result.mean_turnover == pytest.approx(2.0)
    assert result.mean_ic == pytest.approx(0.0)
    assert result.mean_rank_ic == pytest.approx(0.0)

    replay = analyze_factor(
        factor_id="momentum",
        exposures=first_exposures + second_exposures,
        outcomes=first_outcomes + second_outcomes,
        quantile_count=2,
        min_cross_section=4,
    )
    assert replay.analysis_hash == result.analysis_hash


def test_analysis_fails_closed_for_constant_ties_and_missing_outcomes() -> None:
    tied_exposures = _analysis_exposures(
        "tied",
        SESSION_1,
        {symbol: 1.0 for symbol in SYMBOLS},
    )
    outcomes = _analysis_outcomes(
        "tied",
        SESSION_1,
        {"AL_CONT": 0.01, "CU_CONT": 0.02, "RB_CONT": 0.03, "ZN_CONT": 0.04},
    )
    with pytest.raises(FactorResearchError, match="没有足够"):
        analyze_factor(
            factor_id="momentum",
            exposures=tied_exposures,
            outcomes=outcomes,
            quantile_count=2,
            min_cross_section=4,
        )


def test_analysis_rejects_mixed_factor_definition_or_pipeline_identity() -> None:
    exposures = _analysis_exposures(
        "identity",
        SESSION_1,
        {"AL_CONT": 1.0, "CU_CONT": 2.0, "RB_CONT": 3.0, "ZN_CONT": 4.0},
    )
    outcomes = _analysis_outcomes(
        "identity",
        SESSION_1,
        {"AL_CONT": 0.01, "CU_CONT": 0.02, "RB_CONT": 0.03, "ZN_CONT": 0.04},
    )
    mixed_config = (replace(exposures[0], config_hash=_hash("other-config")), *exposures[1:])

    with pytest.raises(FactorResearchError, match="FactorPipelineConfig"):
        analyze_factor(
            factor_id="momentum",
            exposures=mixed_config,
            outcomes=outcomes,
            quantile_count=2,
            min_cross_section=4,
        )

    mixed_definition = (
        replace(exposures[0], factor_definition_hash=_hash("other-definition")),
        *exposures[1:],
    )
    with pytest.raises(FactorResearchError, match="FactorDefinition"):
        analyze_factor(
            factor_id="momentum",
            exposures=mixed_definition,
            outcomes=outcomes,
            quantile_count=2,
            min_cross_section=4,
        )

    complete_exposures = _analysis_exposures(
        "missing",
        SESSION_1,
        {"AL_CONT": 1.0, "CU_CONT": 2.0, "RB_CONT": 3.0, "ZN_CONT": 4.0},
    )
    missing_outcome = _analysis_outcomes(
        "missing",
        SESSION_1,
        {"AL_CONT": 0.01, "CU_CONT": 0.02, "RB_CONT": 0.03},
    )
    with pytest.raises(FactorResearchError, match="不得静默丢弃"):
        analyze_factor(
            factor_id="momentum",
            exposures=complete_exposures,
            outcomes=missing_outcome,
            quantile_count=2,
            min_cross_section=3,
        )


def test_forward_outcome_model_rejects_same_session_evaluation() -> None:
    with pytest.raises(FactorResearchError, match="必须晚于 decision_session"):
        FactorForwardOutcome(
            origin_checkpoint_hash=_hash("origin"),
            decision_session=SESSION_1,
            evaluation_checkpoint_hash=_hash("evaluation"),
            evaluation_session=SESSION_1,
            evaluation_at=_decision_at(SESSION_1),
            symbol="AL_CONT",
            forward_return=0.01,
        )


def test_portfolio_applies_volatility_sizing_and_stays_research_only() -> None:
    config = _pipeline_config(target_volatility=0.02)
    inputs = _portfolio_inputs()
    checkpoint_data = _checkpoint_data(config, checkpoint_id="sizing", **inputs)

    proposal = build_factor_portfolio_proposal(config=config, checkpoint_data=checkpoint_data)
    expected_unscaled = {
        "AL_CONT": -4.0 / 11.0,
        "CU_CONT": -2.0 / 33.0,
        "RB_CONT": 1.0 / 33.0,
        "ZN_CONT": 1.0 / 22.0,
    }
    expected_volatility = math.sqrt(
        sum((expected_unscaled[symbol] * inputs["volatility"][symbol]) ** 2 for symbol in SYMBOLS)
    )
    expected_scale = config.target_volatility / expected_volatility
    weights = {item.symbol: item.target_weight for item in proposal.weights}

    assert isinstance(proposal, FactorPortfolioProposal)
    assert proposal.status is ProposalStatus.PROPOSAL
    assert proposal.config_hash == config.config_hash
    assert proposal.estimated_volatility == pytest.approx(expected_volatility)
    assert proposal.volatility_scale == pytest.approx(expected_scale)
    assert weights == pytest.approx(
        {symbol: value * expected_scale for symbol, value in expected_unscaled.items()}
    )
    assert proposal.candidate_admission_eligible is False
    assert proposal.simnow_handoff_allowed is False


def test_portfolio_enforces_caps_and_honors_alpha_risk_budgets() -> None:
    constrained_config = _pipeline_config(
        alpha_one_budget=0.75,
        target_volatility=1.0,
        max_abs_weight=0.1,
        max_gross_exposure=0.15,
    )
    inputs = _portfolio_inputs()
    constrained_data = _checkpoint_data(
        constrained_config,
        checkpoint_id="constrained",
        **inputs,
    )
    constrained = build_factor_portfolio_proposal(
        config=constrained_config,
        checkpoint_data=constrained_data,
    )
    constrained_weights = {item.symbol: item.target_weight for item in constrained.weights}

    assert constrained.volatility_scale == 1.0
    assert all(abs(weight) <= constrained_config.max_abs_weight for weight in constrained_weights.values())
    assert sum(abs(weight) for weight in constrained_weights.values()) == pytest.approx(
        constrained_config.max_gross_exposure
    )
    assert constrained_weights["AL_CONT"] < 0.0
    assert constrained_weights["ZN_CONT"] > 0.0

    reverse_budget_config = _pipeline_config(
        alpha_one_budget=0.25,
        target_volatility=1.0,
    )
    reverse_budget_data = _checkpoint_data(
        reverse_budget_config,
        checkpoint_id="reverse-budget",
        **inputs,
    )
    reverse_budget = build_factor_portfolio_proposal(
        config=reverse_budget_config,
        checkpoint_data=reverse_budget_data,
    )
    reverse_weights = {item.symbol: item.target_weight for item in reverse_budget.weights}

    assert reverse_weights["AL_CONT"] > 0.0
    assert reverse_weights["ZN_CONT"] < 0.0


def test_portfolio_returns_no_proposal_for_incomplete_factor_inputs() -> None:
    config = _pipeline_config()
    inputs = _portfolio_inputs()
    checkpoint_data = _checkpoint_data(
        config,
        checkpoint_id="incomplete",
        alpha_one=inputs["alpha_one"],
        alpha_two=inputs["alpha_two"],
        volatility={},
    )

    proposal = build_factor_portfolio_proposal(config=config, checkpoint_data=checkpoint_data)

    assert proposal.status is ProposalStatus.NO_PROPOSAL_WARMUP
    assert proposal.no_proposal_reason == "factor_or_volatility_input_incomplete"
    assert proposal.weights == ()
    assert proposal.estimated_volatility is None
    assert proposal.volatility_scale is None
    assert proposal.config_hash == config.config_hash
    assert proposal.candidate_admission_eligible is False
    assert proposal.simnow_handoff_allowed is False


def test_checkpoint_rejects_exposure_not_bound_to_declared_materialization() -> None:
    config = _pipeline_config()
    inputs = _portfolio_inputs()
    checkpoint = _checkpoint_data(config, checkpoint_id="binding", **inputs)
    altered_exposure = replace(
        checkpoint.exposures[0],
        materialization_hash=_hash("unbound-materialization"),
    )

    with pytest.raises(FactorResearchError, match="definition/materialization"):
        FactorCheckpointData(
            checkpoint_hash=checkpoint.checkpoint_hash,
            decision_at=checkpoint.decision_at,
            decision_session=checkpoint.decision_session,
            market_evidence_hash=checkpoint.market_evidence_hash,
            snapshot_id=checkpoint.snapshot_id,
            dataset_version_hash=checkpoint.dataset_version_hash,
            config_hash=checkpoint.config_hash,
            materializations=checkpoint.materializations,
            exposures=(altered_exposure, *checkpoint.exposures[1:]),
            market_slices=checkpoint.market_slices,
        )


def test_no_proposal_checkpoint_explicitly_flattens_research_targets() -> None:
    config = _pipeline_config()
    inputs = _portfolio_inputs()
    first = _checkpoint_data(
        config,
        checkpoint_id="active",
        session=SESSION_1,
        **inputs,
    )
    second = _checkpoint_data(
        config,
        checkpoint_id="rejected",
        session=SESSION_2,
        **inputs,
    )
    active = build_factor_portfolio_proposal(config=config, checkpoint_data=first)
    rejected = FactorPortfolioProposal(
        checkpoint_hash=second.checkpoint_hash,
        decision_at=second.decision_at,
        decision_session=second.decision_session,
        snapshot_id=second.snapshot_id,
        checkpoint_data_hash=second.checkpoint_data_hash,
        config_hash=config.config_hash,
        status=ProposalStatus.NO_PROPOSAL_WARMUP,
        weights=(),
        estimated_volatility=None,
        volatility_scale=None,
        no_proposal_reason="factor_or_volatility_input_incomplete",
    )

    targets = build_factor_target_frame((active, rejected), (first, second))
    rejected_targets = targets.filter(pl.col("date") == SESSION_2).sort("symbol")

    assert rejected_targets.get_column("symbol").to_list() == list(SYMBOLS)
    assert rejected_targets.get_column("target_weight").to_list() == [0.0, 0.0, 0.0, 0.0]


def test_portfolio_rejects_all_tied_alpha_cross_section() -> None:
    config = _pipeline_config()
    inputs = _portfolio_inputs()
    checkpoint_data = _checkpoint_data(
        config,
        checkpoint_id="tied-alpha",
        alpha_one={symbol: 1.0 for symbol in SYMBOLS},
        alpha_two=inputs["alpha_two"],
        volatility=inputs["volatility"],
    )

    with pytest.raises(FactorResearchError, match="没有可用于排序的方差"):
        build_factor_portfolio_proposal(config=config, checkpoint_data=checkpoint_data)


def test_frozen_robustness_plan_rejects_missing_alpha_neighbour_and_cost_drift() -> None:
    config = _pipeline_config()
    missing_alpha_plan = replace(
        config.robustness_plan,
        parameter_variants=(config.robustness_plan.parameter_variants[0],),
    )
    with pytest.raises(FactorResearchError, match="每个 alpha factor"):
        replace(config, robustness_plan=missing_alpha_plan)

    drifted_baseline = replace(
        config.robustness_plan.cost_scenarios[1],
        commission_bps=1.5,
    )
    cost_drift_plan = replace(
        config.robustness_plan,
        cost_scenarios=(config.robustness_plan.cost_scenarios[0], drifted_baseline),
    )
    with pytest.raises(FactorResearchError, match="baseline cost scenario"):
        replace(config, robustness_plan=cost_drift_plan)


def test_robustness_plan_rejects_overlapping_subperiods_before_a_run() -> None:
    plan = _robustness_plan()
    overlapping = replace(
        plan.subperiods[1],
        period=ValidationPeriod(date(2025, 1, 4), date(2025, 1, 8)),
    )
    with pytest.raises(FactorResearchError, match="子样本区间必须严格不重叠"):
        replace(plan, subperiods=(plan.subperiods[0], overlapping))


def _valid_robustness_result(config: FactorPipelineConfig) -> FactorRobustnessResult:
    """Build a complete, threshold-passing result to exercise its invariants."""

    plan = config.robustness_plan
    experiment = FactorResearchExperiment(
        experiment_id=config.pipeline_id,
        config_hash=config.config_hash,
        decision_replay_plan_hash=_hash("robustness-replay-plan"),
        dataset_version_hashes=(_hash("robustness-dataset"),),
        feature_version_hashes=(_hash("robustness-feature-version"),),
        code_revision=config.code_revision,
    )
    scenario_results = tuple(
        FactorRobustnessScenarioResult(
            scenario_id=scenario.scenario_id,
            factor_id=definition.factor_id,
            analysis_hash=_hash(f"scenario-analysis:{scenario.scenario_id}:{definition.factor_id}"),
            analysis_period_count=1,
            mean_rank_ic=0.0,
            positive_ic_fraction=1.0,
            quantile_spread=0.0,
            ic_standard_deviation=0.0,
            mean_turnover=0.0,
            passed=True,
        )
        for scenario in plan.subperiods
        for definition in config.alpha_factors
    )
    parameter_results = tuple(
        FactorRobustnessParameterVariantResult(
            variant_id=variant.variant_id,
            variant_hash=variant.variant_hash,
            factor_id=variant.factor_id,
            config_hash=config.with_parameter_variant(variant).config_hash,
            analysis_hash=_hash(f"parameter-analysis:{variant.variant_id}"),
            analysis_period_count=1,
            mean_rank_ic=0.0,
            positive_ic_fraction=1.0,
            quantile_spread=0.0,
            ic_standard_deviation=0.0,
            mean_turnover=0.0,
            passed=True,
        )
        for variant in plan.parameter_variants
    )
    cost_results = tuple(
        FactorRobustnessCostScenarioResult(
            scenario_id=scenario.scenario_id,
            scenario_hash=scenario.scenario_hash,
            backtest_result_hash=_hash(f"cost-backtest:{scenario.scenario_id}"),
            total_return=0.0,
            max_drawdown=0.0,
            passed=True,
        )
        for scenario in plan.cost_scenarios
    )
    summaries = tuple(
        FactorRobustnessFactorSummary(
            factor_id=definition.factor_id,
            scenario_count=len(plan.subperiods),
            passed_scenario_count=len(plan.subperiods),
            pass_fraction=1.0,
            passed=True,
        )
        for definition in config.alpha_factors
    )
    return FactorRobustnessResult(
        plan=plan,
        config=config,
        experiment=experiment,
        checkpoint_data_hashes=(_hash("robustness-checkpoint"),),
        proposal_hashes=(_hash("robustness-proposal"),),
        outcome_hashes=(_hash("robustness-outcome"),),
        scenario_results=scenario_results,
        parameter_variant_results=parameter_results,
        cost_scenario_results=cost_results,
        factor_summaries=summaries,
    )


def test_robustness_result_binds_exact_frozen_coverage_and_derives_pass_state() -> None:
    config = _pipeline_config()
    result = _valid_robustness_result(config)

    assert result.plan_hash == config.robustness_plan.plan_hash
    assert result.config_hash == config.config_hash
    assert result.passed is True

    with pytest.raises(FactorResearchError, match="精确覆盖 frozen subperiod"):
        FactorRobustnessResult(
            plan=config.robustness_plan,
            config=config,
            experiment=result.experiment,
            checkpoint_data_hashes=result.checkpoint_data_hashes,
            proposal_hashes=result.proposal_hashes,
            outcome_hashes=result.outcome_hashes,
            scenario_results=result.scenario_results[:-1],
            parameter_variant_results=result.parameter_variant_results,
            cost_scenario_results=result.cost_scenario_results,
            factor_summaries=result.factor_summaries,
        )

    with pytest.raises(FactorResearchError, match="必须由 frozen scenario results 派生"):
        FactorRobustnessResult(
            plan=config.robustness_plan,
            config=config,
            experiment=result.experiment,
            checkpoint_data_hashes=result.checkpoint_data_hashes,
            proposal_hashes=result.proposal_hashes,
            outcome_hashes=result.outcome_hashes,
            scenario_results=result.scenario_results,
            parameter_variant_results=result.parameter_variant_results,
            cost_scenario_results=result.cost_scenario_results,
            factor_summaries=(
                replace(result.factor_summaries[0], passed=False),
                *result.factor_summaries[1:],
            ),
        )
