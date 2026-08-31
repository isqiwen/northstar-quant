"""Unit contracts for the one-shot AI factor-mining agent boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from hashlib import sha256

import pytest

from northstar_quant.application.ai_factor_mining import (
    AIFactorMiningAgent,
    AIFactorMiningAgentError,
    AIFactorMiningAgentRequest,
    ai_factor_mining_request_hash,
)
from northstar_quant.application.factor_mining_tools import (
    EvaluateFactorCandidateDiscoveryBatchRequest,
    FactorMiningToolApi,
    FactorMiningToolApiError,
    FactorMiningToolDependencies,
    FactorMiningToolName,
)
from northstar_quant.research.factor_mining.models import (
    FactorCandidateGenerationReceipt,
    FactorCandidateProposal,
    FactorMiningCampaignSpec,
    FactorMiningCostScenario,
    FactorMiningError,
    FactorMiningMultipleTestingControl,
    FactorMiningSelectionPolicy,
    FactorMiningStageBoundaryMode,
    FactorParameterDomain,
    FactorPipelineTemplate,
    FactorPrimitive,
    FactorSearchBudget,
)
from northstar_quant.research.factor_mining.protocol import (
    CandidateDiscoveryDisposition,
    FactorCandidateDiscoveryResult,
    FactorMiningDiscoveryResult,
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
    WalkForwardFold,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _robustness_plan() -> FactorRobustnessPlan:
    return FactorRobustnessPlan(
        plan_id="agent_factor_mining_robustness",
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


def _campaign() -> FactorMiningCampaignSpec:
    folds = (
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
    template = FactorPipelineTemplate(
        template_id="daily_factor_mining",
        version="1.0.0",
        feature_version="1.0.0",
        code_revision="factor-mining-agent-unit",
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
        target_volatility=0.1,
        max_abs_weight=0.3,
        max_gross_exposure=0.8,
        holding_period_sessions=1,
        initial_cash=100_000.0,
        commission_bps=5.0,
        min_commission=1.0,
        slippage_bps=8.0,
        execution_delay_sessions=1,
        walk_forward_folds=folds,
        robustness_plan=_robustness_plan(),
    )
    return FactorMiningCampaignSpec(
        campaign_id="momentum_search",
        selection_at=datetime(2026, 1, 7, 16, tzinfo=UTC),
        decision_replay_plan_hash=_hash("decision-replay-plan"),
        dataset_version_hashes=(_hash("dataset-v1"),),
        template=template,
        primitives=(
            FactorPrimitive(
                primitive_id="momentum_roc",
                feature_id="momentum.roc",
                allowed_directions=(1.0,),
                parameter_domains=(
                    FactorParameterDomain(name="lookback_bars", allowed_values=(1, 2, 3)),
                ),
            ),
        ),
        budget=FactorSearchBudget(max_candidates=2),
        selection_policy=FactorMiningSelectionPolicy(
            policy_id="unit_discovery_selection",
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
            minimum_in_sample_periods=1,
            minimum_validation_periods=1,
            minimum_stage_backtest_sessions=1,
            minimum_in_sample_mean_rank_ic=-1.0,
            minimum_validation_mean_rank_ic=-1.0,
            minimum_validation_quantile_spread=-1.0,
            maximum_validation_factor_turnover=100.0,
            maximum_validation_portfolio_turnover=100.0,
            minimum_validation_total_return=-0.99,
            minimum_validation_max_drawdown=-1.0,
            family_wise_alpha=0.05,
            multiple_testing_control=FactorMiningMultipleTestingControl.BONFERRONI_SIGN_TEST,
            max_selected_candidates=1,
            stage_boundary_mode=FactorMiningStageBoundaryMode.FLAT_START_FORCED_CLOSE,
        ),
        generator_id="fake_generator",
        generator_model_revision_hash=_hash("model-revision"),
        prompt_template_hash=_hash("prompt-template"),
    )


def _receipt(campaign: FactorMiningCampaignSpec) -> FactorCandidateGenerationReceipt:
    proposal = FactorCandidateProposal.create(
        campaign_id=campaign.campaign_id,
        candidate_id="candidate_one",
        primitive_id="momentum_roc",
        direction=1.0,
        parameters={"lookback_bars": 2},
    )
    return FactorCandidateGenerationReceipt(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        generator_id=campaign.generator_id,
        generator_model_revision_hash=campaign.generator_model_revision_hash,
        prompt_template_hash=campaign.prompt_template_hash,
        provider_output_hash=_hash("provider-output"),
        proposals=(proposal,),
    )


def _two_candidate_receipt(campaign: FactorMiningCampaignSpec) -> FactorCandidateGenerationReceipt:
    first = _receipt(campaign).proposals[0]
    second = FactorCandidateProposal.create(
        campaign_id=campaign.campaign_id,
        candidate_id="candidate_two",
        primitive_id="momentum_roc",
        direction=1.0,
        parameters={"lookback_bars": 3},
    )
    return FactorCandidateGenerationReceipt(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        generator_id=campaign.generator_id,
        generator_model_revision_hash=campaign.generator_model_revision_hash,
        prompt_template_hash=campaign.prompt_template_hash,
        provider_output_hash=_hash("provider-output-two-candidates"),
        proposals=(first, second),
    )


@dataclass
class FakeGenerator:
    receipt: FactorCandidateGenerationReceipt
    calls: list[object] = field(default_factory=list)

    def generate(self, request: object) -> FactorCandidateGenerationReceipt:
        self.calls.append(request)
        return self.receipt


@dataclass
class FakeCampaignPort:
    selection_policy_hash: str
    calls: list[EvaluateFactorCandidateDiscoveryBatchRequest] = field(default_factory=list)

    def evaluate_discovery_candidate_batch(
        self,
        *,
        request: EvaluateFactorCandidateDiscoveryBatchRequest,
    ) -> FactorMiningDiscoveryResult:
        self.calls.append(request)
        candidate = request.generation.proposals[0]
        discovery = FactorCandidateDiscoveryResult(
            campaign_id=request.generation.campaign_id,
            campaign_hash=request.generation.campaign_hash,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            disposition=CandidateDiscoveryDisposition.REJECTED_INPUT,
            reason_code="TEST_REJECTED",
            validation_hash=_hash("validation"),
        )
        return FactorMiningDiscoveryResult(
            campaign_id=request.generation.campaign_id,
            campaign_hash=request.generation.campaign_hash,
            generation_receipt_hash=request.generation.receipt_hash,
            selection_policy_hash=self.selection_policy_hash,
            results=(discovery,),
        )


def test_agent_runs_a_single_redacted_generation_then_the_closed_research_tool() -> None:
    campaign = _campaign()
    generator = FakeGenerator(_receipt(campaign))
    port = FakeCampaignPort(campaign.selection_policy.policy_hash)
    agent = AIFactorMiningAgent(
        generator=generator,
        tool_api=FactorMiningToolApi(FactorMiningToolDependencies(campaign_port=port)),
    )
    request = AIFactorMiningAgentRequest(run_id="factor_mining_run", campaign=campaign)

    result = agent.run(request)

    assert ai_factor_mining_request_hash(request) == result.request_hash
    assert len(generator.calls) == 1
    assert len(port.calls) == 1
    assert port.calls[0].generation.receipt_hash == result.generation.receipt_hash
    assert result.lifecycle == "RESEARCH_ONLY"
    assert result.research_only is True
    assert result.candidate_admission_eligible is False
    assert result.simnow_handoff_allowed is False
    assert result.discovery_result.research_only is True
    assert not hasattr(result.discovery_result, "run_manifest_hash")


def test_agent_reserves_before_generation_and_refuses_automatic_retry() -> None:
    campaign = _campaign()
    generator = FakeGenerator(_receipt(campaign))
    port = FakeCampaignPort(campaign.selection_policy.policy_hash)
    agent = AIFactorMiningAgent(
        generator=generator,
        tool_api=FactorMiningToolApi(FactorMiningToolDependencies(campaign_port=port)),
    )
    request = AIFactorMiningAgentRequest(run_id="factor_mining_run", campaign=campaign)

    agent.run(request)
    with pytest.raises(AIFactorMiningAgentError, match="cannot be automatically"):
        agent.run(request)

    assert len(generator.calls) == 1
    assert len(port.calls) == 1


def test_receipt_mismatch_fails_before_the_campaign_port_is_called() -> None:
    campaign = _campaign()
    receipt = _receipt(campaign)
    bad_receipt = FactorCandidateGenerationReceipt(
        campaign_id=receipt.campaign_id,
        campaign_hash=receipt.campaign_hash,
        generator_id=receipt.generator_id,
        generator_model_revision_hash=_hash("different-model"),
        prompt_template_hash=receipt.prompt_template_hash,
        provider_output_hash=receipt.provider_output_hash,
        proposals=receipt.proposals,
    )
    generator = FakeGenerator(bad_receipt)
    port = FakeCampaignPort(campaign.selection_policy.policy_hash)
    agent = AIFactorMiningAgent(
        generator=generator,
        tool_api=FactorMiningToolApi(FactorMiningToolDependencies(campaign_port=port)),
    )

    with pytest.raises(FactorMiningError, match="does not exactly match"):
        agent.run(AIFactorMiningAgentRequest(run_id="factor_mining_run", campaign=campaign))

    assert len(generator.calls) == 1
    assert port.calls == []


def test_tool_api_rejects_a_campaign_port_that_omits_a_submitted_candidate() -> None:
    campaign = _campaign()
    port = FakeCampaignPort(campaign.selection_policy.policy_hash)
    tool_api = FactorMiningToolApi(FactorMiningToolDependencies(campaign_port=port))

    with pytest.raises(FactorMiningToolApiError, match="one exact result"):
        tool_api.evaluate_discovery_candidate_batch(
            EvaluateFactorCandidateDiscoveryBatchRequest(
                generation=_two_candidate_receipt(campaign)
            )
        )

    assert len(port.calls) == 1


def test_ai_facing_surface_cannot_commit_selection_or_release_oos() -> None:
    campaign = _campaign()
    port = FakeCampaignPort(campaign.selection_policy.policy_hash)
    tool_api = FactorMiningToolApi(FactorMiningToolDependencies(campaign_port=port))
    agent = AIFactorMiningAgent(generator=FakeGenerator(_receipt(campaign)), tool_api=tool_api)

    assert set(FactorMiningToolName) == {
        FactorMiningToolName.EVALUATE_FACTOR_CANDIDATE_DISCOVERY_BATCH
    }
    assert not hasattr(tool_api, "commit_selection")
    assert not hasattr(tool_api, "release_oos")
    assert not hasattr(agent, "commit_selection")
    assert not hasattr(agent, "release_oos")
