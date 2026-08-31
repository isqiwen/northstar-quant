"""Unit contracts for bounded, declarative AI factor-mining inputs."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256

import pytest

from northstar_quant.research.factor_mining import (
    CandidateDiscoveryDisposition,
    CandidateValidationStatus,
    FactorCandidateDiscoveryResult,
    FactorCandidateGenerationReceipt,
    FactorCandidateProposal,
    FactorDiscoveryStageCostResult,
    FactorMiningCampaignSpec,
    FactorMiningCostScenario,
    FactorMiningDiscoveryResult,
    FactorMiningError,
    FactorMiningMultipleTestingControl,
    FactorMiningOOSRelease,
    FactorMiningOOSReleaseResult,
    FactorMiningSelectionDisposition,
    FactorMiningSelectionPolicy,
    FactorMiningStageBoundaryMode,
    FactorMiningStageEvidence,
    FactorParameterDomain,
    FactorPipelineTemplate,
    FactorPrimitive,
    FactorSearchBudget,
    select_discovery_candidates,
    validate_factor_candidate,
)
from northstar_quant.research.factors import (
    FactorDefinition,
    FactorRobustnessCostScenario,
    FactorRobustnessParameterVariant,
    FactorRobustnessPlan,
    FactorRobustnessSubperiod,
    FactorRole,
    FactorStabilityThresholds,
)
from northstar_quant.research.validation.framework import (
    ValidationPeriod,
    ValidationSplit,
    ValidationStage,
    WalkForwardFold,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _folds() -> tuple[WalkForwardFold, WalkForwardFold]:
    return (
        WalkForwardFold(
            fold_id="fold_01",
            split=ValidationSplit(
                in_sample=ValidationPeriod(date(2026, 1, 5), date(2026, 1, 6)),
                validation=ValidationPeriod(date(2026, 1, 7), date(2026, 1, 7)),
                out_of_sample=ValidationPeriod(date(2026, 1, 8), date(2026, 1, 9)),
            ),
        ),
        WalkForwardFold(
            fold_id="fold_02",
            split=ValidationSplit(
                in_sample=ValidationPeriod(date(2026, 1, 5), date(2026, 1, 6)),
                validation=ValidationPeriod(date(2026, 1, 7), date(2026, 1, 7)),
                out_of_sample=ValidationPeriod(date(2026, 1, 10), date(2026, 1, 11)),
            ),
        ),
    )


def _robustness_plan(
    *,
    parameter_name: str = "lookback_bars",
) -> FactorRobustnessPlan:
    return FactorRobustnessPlan(
        plan_id="factor_mining_robustness",
        version="1.0.0",
        subperiods=(
            FactorRobustnessSubperiod(
                scenario_id="early",
                period=ValidationPeriod(date(2026, 1, 8), date(2026, 1, 9)),
                excluded_symbols=(),
            ),
            FactorRobustnessSubperiod(
                scenario_id="late_exclude_al",
                period=ValidationPeriod(date(2026, 1, 10), date(2026, 1, 11)),
                excluded_symbols=("AL_CONT",),
            ),
        ),
        parameter_variants=(
            FactorRobustnessParameterVariant.create(
                variant_id="candidate_lookback_1",
                factor_id="candidate_alpha",
                parameters={parameter_name: 1},
            ),
            FactorRobustnessParameterVariant.create(
                variant_id="candidate_lookback_2",
                factor_id="candidate_alpha",
                parameters={parameter_name: 2},
            ),
            FactorRobustnessParameterVariant.create(
                variant_id="candidate_lookback_3",
                factor_id="candidate_alpha",
                parameters={parameter_name: 3},
            ),
        ),
        cost_scenarios=(
            FactorRobustnessCostScenario(
                scenario_id="adverse",
                commission_bps=10.0,
                min_commission=2.0,
                slippage_bps=16.0,
                execution_delay_sessions=1,
            ),
            FactorRobustnessCostScenario(
                scenario_id="baseline",
                commission_bps=5.0,
                min_commission=1.0,
                slippage_bps=8.0,
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


def _template(*, parameter_name: str = "lookback_bars") -> FactorPipelineTemplate:
    return FactorPipelineTemplate(
        template_id="daily_factor_mining",
        version="1.0.0",
        feature_version="1.0.0",
        code_revision="factor-mining-unit",
        risk_model_factor=FactorDefinition.create(
            factor_id="realized_volatility",
            feature_id="technical.realized_volatility",
            role=FactorRole.RISK_MODEL,
            direction=1.0,
            risk_budget=0.0,
            parameters={"window_bars": 2},
        ),
        min_cross_section=4,
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
        walk_forward_folds=_folds(),
        robustness_plan=_robustness_plan(parameter_name=parameter_name),
    )


def _selection_policy() -> FactorMiningSelectionPolicy:
    return FactorMiningSelectionPolicy(
        policy_id="development_only_selection",
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
                commission_bps=5.0,
                min_commission=1.0,
                slippage_bps=8.0,
                execution_delay_sessions=1,
            ),
        ),
        minimum_in_sample_periods=2,
        minimum_validation_periods=2,
        minimum_stage_backtest_sessions=2,
        minimum_in_sample_mean_rank_ic=-1.0,
        minimum_validation_mean_rank_ic=-1.0,
        minimum_validation_quantile_spread=-1.0,
        maximum_validation_factor_turnover=1.0,
        maximum_validation_portfolio_turnover=1.0,
        minimum_validation_total_return=-0.99,
        minimum_validation_max_drawdown=-0.80,
        family_wise_alpha=0.99,
        multiple_testing_control=FactorMiningMultipleTestingControl.BONFERRONI_SIGN_TEST,
        max_selected_candidates=1,
        stage_boundary_mode=FactorMiningStageBoundaryMode.FLAT_START_FORCED_CLOSE,
    )


def _primitive(*, values: tuple[int, ...] = (1, 2, 3)) -> FactorPrimitive:
    return FactorPrimitive(
        primitive_id="momentum_roc",
        feature_id="momentum.roc",
        allowed_directions=(-1.0, 1.0),
        parameter_domains=(
            FactorParameterDomain(name="lookback_bars", allowed_values=values),
        ),
    )


def _campaign() -> FactorMiningCampaignSpec:
    return FactorMiningCampaignSpec(
        campaign_id="momentum_search",
        selection_at=datetime(2026, 1, 7, 16, tzinfo=UTC),
        decision_replay_plan_hash=_hash("decision-replay-plan"),
        dataset_version_hashes=(_hash("dataset-v1"),),
        template=_template(),
        primitives=(_primitive(),),
        budget=FactorSearchBudget(max_candidates=3),
        selection_policy=_selection_policy(),
        generator_id="offline_test_generator",
        generator_model_revision_hash=_hash("model-revision"),
        prompt_template_hash=_hash("prompt-template"),
    )


def _candidate(
    *,
    candidate_id: str = "candidate_one",
    primitive_id: str = "momentum_roc",
    lookback_bars: int = 2,
) -> FactorCandidateProposal:
    return FactorCandidateProposal.create(
        campaign_id="momentum_search",
        candidate_id=candidate_id,
        primitive_id=primitive_id,
        direction=1.0,
        parameters={"lookback_bars": lookback_bars},
    )


def _validated_candidate(
    campaign: FactorMiningCampaignSpec,
    candidate: FactorCandidateProposal,
):
    validation = validate_factor_candidate(campaign=campaign, candidate=candidate)
    assert validation.status is CandidateValidationStatus.VALIDATED_FOR_RESEARCH
    assert validation.factor_definition is not None
    return validation


def _stage_evidence(
    *,
    campaign: FactorMiningCampaignSpec,
    candidate: FactorCandidateProposal,
    stage: ValidationStage,
    mean_rank_ic: float,
) -> FactorMiningStageEvidence:
    validation = _validated_candidate(campaign, candidate)
    assert validation.factor_definition is not None
    config = campaign.template.build_config(
        campaign_id=campaign.campaign_id,
        candidate_id=candidate.candidate_id,
        factor_definition=validation.factor_definition,
    )
    fold = campaign.template.walk_forward_folds[0]
    stage_label = stage.value
    costs = tuple(
        sorted(
            (
                FactorDiscoveryStageCostResult(
                    cost_scenario_hash=scenario.scenario_hash,
                    backtest_result_hash=_hash(
                        f"{candidate.candidate_id}:{stage_label}:{scenario.scenario_id}:backtest"
                    ),
                    session_count=2,
                    total_return=0.02,
                    max_drawdown=-0.05,
                    portfolio_turnover=0.10,
                )
                for scenario in campaign.selection_policy.cost_scenarios
            ),
            key=lambda item: item.cost_scenario_hash,
        )
    )
    periods = {
        ValidationStage.IN_SAMPLE: ("2026-01-05", "2026-01-06"),
        ValidationStage.VALIDATION: ("2026-01-07", "2026-01-08"),
        ValidationStage.OUT_OF_SAMPLE: ("2026-01-09", "2026-01-10"),
    }
    period_start, period_end = periods[stage]
    return FactorMiningStageEvidence(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.candidate_hash,
        factor_definition_hash=validation.factor_definition.definition_hash,
        pipeline_config_hash=config.config_hash,
        fold_id=fold.fold_id,
        fold_hash=fold.fold_hash,
        stage=stage,
        period_start=period_start,
        period_end=period_end,
        analysis_hash=_hash(f"{candidate.candidate_id}:{stage_label}:analysis"),
        analysis_period_hashes=(
            _hash(f"{candidate.candidate_id}:{stage_label}:period:one"),
            _hash(f"{candidate.candidate_id}:{stage_label}:period:two"),
        ),
        outcome_hashes=(
            _hash(f"{candidate.candidate_id}:{stage_label}:outcome:one"),
            _hash(f"{candidate.candidate_id}:{stage_label}:outcome:two"),
        ),
        mean_rank_ic=mean_rank_ic,
        quantile_spread=0.03,
        mean_factor_turnover=0.05,
        positive_rank_ic_count=2,
        purged_cross_boundary_outcome_count=1,
        cost_results=costs,
        stage_boundary_mode=campaign.selection_policy.stage_boundary_mode,
    )


def _evaluated_discovery(
    *,
    campaign: FactorMiningCampaignSpec,
    candidate: FactorCandidateProposal,
    mean_rank_ic: float,
) -> FactorCandidateDiscoveryResult:
    validation = _validated_candidate(campaign, candidate)
    assert validation.factor_definition is not None
    config = campaign.template.build_config(
        campaign_id=campaign.campaign_id,
        candidate_id=candidate.candidate_id,
        factor_definition=validation.factor_definition,
    )
    return FactorCandidateDiscoveryResult(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.candidate_hash,
        disposition=CandidateDiscoveryDisposition.DISCOVERY_EVALUATED,
        reason_code="DISCOVERY_EVALUATED",
        validation_hash=validation.validation_hash,
        factor_definition_hash=validation.factor_definition.definition_hash,
        pipeline_config_hash=config.config_hash,
        discovery_replay_hash=_hash(f"{candidate.candidate_id}:discovery-replay"),
        stage_evidence=(
            _stage_evidence(
                campaign=campaign,
                candidate=candidate,
                stage=ValidationStage.IN_SAMPLE,
                mean_rank_ic=mean_rank_ic,
            ),
            _stage_evidence(
                campaign=campaign,
                candidate=candidate,
                stage=ValidationStage.VALIDATION,
                mean_rank_ic=mean_rank_ic,
            ),
        ),
    )


def test_campaign_and_parameter_domain_hashes_are_stable_under_safe_input_ordering() -> None:
    first = _primitive(values=(1, 2, 3))
    second = _primitive(values=(3, 1, 2))

    assert first.primitive_hash == second.primitive_hash
    assert first.parameter_domains[0].allowed_values == (1, 2, 3)
    assert _campaign().campaign_hash == _campaign().campaign_hash


def test_campaign_rejects_frozen_robustness_variants_with_mixed_parameter_schemas() -> None:
    base = _campaign()
    mixed_schema_plan = replace(
        base.template.robustness_plan,
        parameter_variants=(
            FactorRobustnessParameterVariant.create(
                variant_id="candidate_lookback_1",
                factor_id="candidate_alpha",
                parameters={"lookback_bars": 1},
            ),
            FactorRobustnessParameterVariant.create(
                variant_id="candidate_window_2",
                factor_id="candidate_alpha",
                parameters={"window_bars": 2},
            ),
        ),
    )

    with pytest.raises(FactorMiningError, match="share exactly one parameter schema"):
        replace(base, template=replace(base.template, robustness_plan=mixed_schema_plan))


def test_campaign_rejects_primitive_schema_that_cannot_run_frozen_robustness_plan() -> None:
    base = _campaign()
    incompatible_primitive = FactorPrimitive(
        primitive_id="volume_ratio",
        feature_id="technical.volume_ratio",
        allowed_directions=(-1.0, 1.0),
        parameter_domains=(
            FactorParameterDomain(name="window_bars", allowed_values=(1, 2, 3)),
        ),
    )

    with pytest.raises(FactorMiningError, match="must exactly match the frozen robustness"):
        replace(base, primitives=(base.primitives[0], incompatible_primitive))


def test_campaign_rejects_frozen_robustness_point_outside_primitive_domain() -> None:
    base = _campaign()
    out_of_grid_plan = replace(
        base.template.robustness_plan,
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
                variant_id="candidate_lookback_4",
                factor_id="candidate_alpha",
                parameters={"lookback_bars": 4},
            ),
        ),
    )

    with pytest.raises(FactorMiningError, match="outside primitive momentum_roc finite domain"):
        replace(base, template=replace(base.template, robustness_plan=out_of_grid_plan))


def test_valid_candidate_is_converted_to_one_alpha_under_the_host_owned_template() -> None:
    campaign = _campaign()
    candidate = _candidate()

    validation = validate_factor_candidate(campaign=campaign, candidate=candidate)
    assert validation.status is CandidateValidationStatus.VALIDATED_FOR_RESEARCH
    assert validation.factor_definition is not None
    assert validation.factor_definition.feature_id == "momentum.roc"
    assert validation.factor_definition.risk_budget == 1.0

    config = campaign.template.build_config(
        campaign_id=campaign.campaign_id,
        candidate_id=candidate.candidate_id,
        factor_definition=validation.factor_definition,
    )
    assert len(config.alpha_factors) == 1
    assert config.volatility_factor == campaign.template.risk_model_factor
    assert config.commission_bps == campaign.template.commission_bps
    assert config.walk_forward_folds == campaign.template.walk_forward_folds


def test_unknown_primitive_and_out_of_grid_parameter_are_reason_coded_rejections() -> None:
    campaign = _campaign()

    unknown = validate_factor_candidate(
        campaign=campaign,
        candidate=_candidate(primitive_id="unknown_primitive"),
    )
    out_of_grid = validate_factor_candidate(
        campaign=campaign,
        candidate=_candidate(lookback_bars=99),
    )

    assert (unknown.status, unknown.reason_code, unknown.factor_definition) == (
        CandidateValidationStatus.REJECTED,
        "UNKNOWN_PRIMITIVE",
        None,
    )
    assert (out_of_grid.status, out_of_grid.reason_code, out_of_grid.factor_definition) == (
        CandidateValidationStatus.REJECTED,
        "PARAMETER_VALUE_NOT_ALLOWED",
        None,
    )


def test_candidate_contract_rejects_latest_source_like_values_and_non_scalar_payloads() -> None:
    with pytest.raises(FactorMiningError, match="forbidden executable selector"):
        FactorCandidateProposal.create(
            campaign_id="momentum_search",
            candidate_id="candidate_one",
            primitive_id="momentum_roc",
            direction=1.0,
            parameters={"lookback_bars": "latest"},
        )
    with pytest.raises(FactorMiningError, match="integer, finite float, or bounded enum"):
        FactorCandidateProposal.create(
            campaign_id="momentum_search",
            candidate_id="candidate_one",
            primitive_id="momentum_roc",
            direction=1.0,
            parameters={"lookback_bars": {"python": "exec"}},
        )


def test_generation_receipt_must_match_all_pinned_generator_inputs_and_budget() -> None:
    campaign = _campaign()
    receipt = FactorCandidateGenerationReceipt(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        generator_id=campaign.generator_id,
        generator_model_revision_hash=campaign.generator_model_revision_hash,
        prompt_template_hash=campaign.prompt_template_hash,
        provider_output_hash=_hash("provider-output"),
        proposals=(_candidate(),),
    )
    receipt.require_campaign(campaign)

    mismatched = FactorCandidateGenerationReceipt(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        generator_id=campaign.generator_id,
        generator_model_revision_hash=_hash("different-model"),
        prompt_template_hash=campaign.prompt_template_hash,
        provider_output_hash=_hash("provider-output-2"),
        proposals=(_candidate(),),
    )
    with pytest.raises(FactorMiningError, match="does not exactly match"):
        mismatched.require_campaign(campaign)


def test_discovery_records_are_permanently_research_only_and_cannot_carry_oos() -> None:
    campaign = _campaign()
    candidate = _candidate()
    validation = validate_factor_candidate(campaign=campaign, candidate=candidate)
    result = FactorCandidateDiscoveryResult(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.candidate_hash,
        disposition=CandidateDiscoveryDisposition.REJECTED_INPUT,
        reason_code="UNKNOWN_PRIMITIVE",
        validation_hash=validation.validation_hash,
    )

    assert result.research_only is True
    assert result.candidate_admission_eligible is False
    assert result.simnow_handoff_allowed is False
    assert not hasattr(result, "run_manifest_hash")
    assert not hasattr(result, "walk_forward_result_hashes")

    with pytest.raises(FactorMiningError, match="cannot contain out-of-sample evidence"):
        FactorCandidateDiscoveryResult(
            campaign_id=campaign.campaign_id,
            campaign_hash=campaign.campaign_hash,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            disposition=CandidateDiscoveryDisposition.DISCOVERY_EVALUATED,
            reason_code="DISCOVERY_EVALUATED",
            validation_hash=validation.validation_hash,
            factor_definition_hash=_hash("definition"),
            pipeline_config_hash=_hash("config"),
            discovery_replay_hash=_hash("discovery-replay"),
            stage_evidence=(
                _stage_evidence(
                    campaign=campaign,
                    candidate=candidate,
                    stage=ValidationStage.OUT_OF_SAMPLE,
                    mean_rank_ic=0.50,
                ),
            ),
        )


def test_selection_commitment_is_deterministic_and_oos_is_explicitly_separate() -> None:
    campaign = _campaign()
    lower_ranked = _candidate(candidate_id="candidate_one", lookback_bars=1)
    higher_ranked = _candidate(candidate_id="candidate_two", lookback_bars=2)
    discovery = FactorMiningDiscoveryResult(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        generation_receipt_hash=_hash("generation-receipt"),
        selection_policy_hash=campaign.selection_policy.policy_hash,
        results=(
            _evaluated_discovery(
                campaign=campaign,
                candidate=lower_ranked,
                mean_rank_ic=0.40,
            ),
            _evaluated_discovery(
                campaign=campaign,
                candidate=higher_ranked,
                mean_rank_ic=0.80,
            ),
        ),
    )

    first = select_discovery_candidates(campaign=campaign, discovery=discovery)
    second = select_discovery_candidates(campaign=campaign, discovery=discovery)

    assert first.commitment_hash == second.commitment_hash
    assert [record.candidate_id for record in first.records] == [
        "candidate_one",
        "candidate_two",
    ]
    assert [(record.candidate_id, record.disposition, record.rank) for record in first.records] == [
        ("candidate_one", FactorMiningSelectionDisposition.NOT_SELECTED, None),
        (
            "candidate_two",
            FactorMiningSelectionDisposition.SELECTED_FOR_OOS_RELEASE,
            1,
        ),
    ]
    assert not hasattr(discovery, "run_manifest_hash")
    assert all(
        stage.stage is not ValidationStage.OUT_OF_SAMPLE
        for candidate_result in discovery.results
        for stage in candidate_result.stage_evidence
    )

    selected = first.selected_records[0]
    validation = _validated_candidate(campaign, higher_ranked)
    assert validation.factor_definition is not None
    config = campaign.template.build_config(
        campaign_id=campaign.campaign_id,
        candidate_id=higher_ranked.candidate_id,
        factor_definition=validation.factor_definition,
    )
    release = FactorMiningOOSRelease(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        selection_commitment_hash=first.commitment_hash,
        results=(
            FactorMiningOOSReleaseResult(
                campaign_id=campaign.campaign_id,
                campaign_hash=campaign.campaign_hash,
                candidate_id=higher_ranked.candidate_id,
                candidate_hash=higher_ranked.candidate_hash,
                selection_record_hash=selected.record_hash,
                factor_definition_hash=validation.factor_definition.definition_hash,
                pipeline_config_hash=config.config_hash,
                run_manifest_hash=_hash("oos-run-manifest"),
                lookahead_certificate_hash=_hash("oos-lookahead-certificate"),
                stage_evidence=(
                    _stage_evidence(
                        campaign=campaign,
                        candidate=higher_ranked,
                        stage=ValidationStage.OUT_OF_SAMPLE,
                        mean_rank_ic=0.90,
                    ),
                ),
            ),
        ),
    )

    assert release.research_only is True
    assert release.results[0].candidate_id == "candidate_two"
    assert release.results[0].stage_evidence[0].stage is ValidationStage.OUT_OF_SAMPLE


@pytest.mark.statistical
def test_oos_perturbation_cannot_change_development_selection_commitment() -> None:
    """Extreme released OOS outcomes are not an input to discovery selection."""

    campaign = _campaign()
    lower_ranked = _candidate(candidate_id="candidate_one", lookback_bars=1)
    higher_ranked = _candidate(candidate_id="candidate_two", lookback_bars=2)
    discovery = FactorMiningDiscoveryResult(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        generation_receipt_hash=_hash("oos-perturbation-receipt"),
        selection_policy_hash=campaign.selection_policy.policy_hash,
        results=(
            _evaluated_discovery(
                campaign=campaign,
                candidate=lower_ranked,
                mean_rank_ic=0.20,
            ),
            _evaluated_discovery(
                campaign=campaign,
                candidate=higher_ranked,
                mean_rank_ic=0.90,
            ),
        ),
    )
    before_release = select_discovery_candidates(campaign=campaign, discovery=discovery)
    selected = before_release.selected_records[0]
    selected_validation = _validated_candidate(campaign, higher_ranked)
    assert selected_validation.factor_definition is not None
    selected_config = campaign.template.build_config(
        campaign_id=campaign.campaign_id,
        candidate_id=higher_ranked.candidate_id,
        factor_definition=selected_validation.factor_definition,
    )

    # Deliberately adverse OOS evidence is constructible only after a
    # commitment and is not an argument to select_discovery_candidates, so it
    # cannot change the already-frozen development ranking.
    unrelated_oos = FactorMiningOOSRelease(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        selection_commitment_hash=before_release.commitment_hash,
        results=(
            FactorMiningOOSReleaseResult(
                campaign_id=campaign.campaign_id,
                campaign_hash=campaign.campaign_hash,
                candidate_id=higher_ranked.candidate_id,
                candidate_hash=higher_ranked.candidate_hash,
                selection_record_hash=selected.record_hash,
                factor_definition_hash=selected_validation.factor_definition.definition_hash,
                pipeline_config_hash=selected_config.config_hash,
                run_manifest_hash=_hash("perturbed-oos-manifest"),
                lookahead_certificate_hash=_hash("perturbed-oos-certificate"),
                stage_evidence=(
                    _stage_evidence(
                        campaign=campaign,
                        candidate=higher_ranked,
                        stage=ValidationStage.OUT_OF_SAMPLE,
                        mean_rank_ic=-1.0,
                    ),
                ),
            ),
        ),
    )

    after_release = select_discovery_candidates(campaign=campaign, discovery=discovery)

    assert unrelated_oos.results[0].stage_evidence[0].mean_rank_ic == -1.0
    assert after_release.commitment_hash == before_release.commitment_hash
    assert after_release.selected_records == before_release.selected_records


def test_volume_ratio_is_a_bounded_ai_factor_mining_primitive() -> None:
    base = _campaign()
    campaign = replace(
        base,
        template=_template(parameter_name="window_bars"),
        primitives=(
            FactorPrimitive(
                primitive_id="volume_ratio",
                feature_id="technical.volume_ratio",
                allowed_directions=(-1.0, 1.0),
                parameter_domains=(
                    FactorParameterDomain(
                        name="window_bars",
                        allowed_values=(1, 2, 3),
                    ),
                ),
            ),
        ),
    )
    accepted = FactorCandidateProposal.create(
        campaign_id=campaign.campaign_id,
        candidate_id="volume_ratio_two_bar_long",
        primitive_id="volume_ratio",
        direction=1.0,
        parameters={"window_bars": 2},
    )
    rejected = FactorCandidateProposal.create(
        campaign_id=campaign.campaign_id,
        candidate_id="volume_ratio_out_of_grid",
        primitive_id="volume_ratio",
        direction=1.0,
        parameters={"window_bars": 4},
    )

    valid = validate_factor_candidate(campaign=campaign, candidate=accepted)
    invalid = validate_factor_candidate(campaign=campaign, candidate=rejected)

    assert valid.status is CandidateValidationStatus.VALIDATED_FOR_RESEARCH
    assert valid.factor_definition is not None
    assert valid.factor_definition.feature_id == "technical.volume_ratio"
    config = campaign.template.build_config(
        campaign_id=campaign.campaign_id,
        candidate_id=accepted.candidate_id,
        factor_definition=valid.factor_definition,
    )
    assert config.alpha_factors[0].feature_id == "technical.volume_ratio"
    assert {
        item.factor_id for item in config.robustness_plan.parameter_variants
    } == {valid.factor_definition.factor_id}
    assert invalid.status is CandidateValidationStatus.REJECTED
    assert invalid.reason_code == "PARAMETER_VALUE_NOT_ALLOWED"
