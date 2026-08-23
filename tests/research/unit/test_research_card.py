"""P2-WP08 Research Cards are reproducible evidence summaries, never trade authority."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import polars as pl
import pytest

from northstar_quant.research.backtest.models import (
    BacktestAssumptions,
    BacktestCodeReference,
    BacktestDataInputKind,
    BacktestDataReference,
    BacktestEngine,
    BacktestRequest,
    BacktestResult,
    RunManifest,
    TargetFrameReference,
)
from northstar_quant.research.reports import ProductContribution, ResearchCard, ResearchReportError
from northstar_quant.research.validation.admission import ResearchAdmissionResult
from northstar_quant.research.validation.framework import (
    ParameterNeighbor,
    ResearchInputEvidenceKind,
    ResearchValidationEvidence,
    ReturnObservation,
    RollingWindow,
    StressKind,
    StressScenario,
    ValidationPeriod,
    ValidationReturnSeries,
    ValidationSplit,
    WalkForwardFold,
    evaluate_validation,
)
from northstar_quant.research.validation.research_decision import (
    HumanResearchApproval,
    ResearchDecision,
    ResearchDecisionEvidence,
    ResearchDecisionState,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _manifest() -> RunManifest:
    request = BacktestRequest(
        engine=BacktestEngine.WEIGHT_RETURN,
        profile_id="offline-profile",
        profile_config_sha256=_hash("profile"),
        profile_dimension_key="CN|FUTURES|1d|1d|trend_following",
        source_frequency="1d",
        signal_frequency="1d",
        execution_frequency="1d",
        settlement_frequency="1d_eod",
        result_frequency="1d_eod",
        selected_strategy_ids=("futures_trend",),
        target=TargetFrameReference.from_frame(
            pl.DataFrame(
                {"date": ["2024-01-02"], "symbol": ["RB_CONT"], "target_weight": [0.1]}
            ),
            time_column="date",
        ),
        data=BacktestDataReference(
            input_kind=BacktestDataInputKind.LEGACY_MARKET_PROJECTION,
            dataset_id="research-dataset",
            source_id="fixture-source",
            adapter_id="fixture-adapter",
            content_sha256=_hash("dataset"),
            schema_version="market_data_v2",
            source_config_sha256=_hash("source"),
        ),
        assumptions=BacktestAssumptions(
            initial_cash=100_000,
            commission_bps=1,
            min_commission=2,
            slippage_bps=3,
            slippage_ticks=1,
            max_volume_participation=0.2,
            lot_size=1,
            execution_delay_sessions=1,
            sellable_after_sessions=0,
            order_ttl_bars=2,
            queue_ahead_ratio=0.1,
        ),
        code=BacktestCodeReference(
            package_version="0.0.test",
            git_commit="deadbeef",
            git_dirty=False,
            worktree_sha256=_hash("worktree"),
        ),
    )
    result = BacktestResult(
        engine=BacktestEngine.WEIGHT_RETURN,
        total_return=0.10,
        annualized_return=0.20,
        max_drawdown=-0.05,
        turnover_estimate=0.30,
        equity_curve=(
            {"date": "2024-01-02", "equity": 1.0},
            {"date": "2024-01-03", "equity": 1.1},
        ),
    ).bind_request(request)
    return RunManifest.create(
        request=request,
        result=result,
        analytics={"equity": 1.1},
        metrics={"total_return": 0.1},
        admission={"status": "INSUFFICIENT_EVIDENCE", "blocking_check_count": 1},
    )


def _validation_and_decision(manifest: RunManifest):
    start = datetime(2024, 1, 1, tzinfo=UTC).date()
    series = ValidationReturnSeries.create(
        ReturnObservation(
            session=start + timedelta(days=index),
            net_return=0.003 if index % 4 else -0.001,
            regime="risk_on" if index % 2 else "risk_off",
        )
        for index in range(72)
    )
    split = ValidationSplit(
        in_sample=ValidationPeriod(start, start + timedelta(days=23)),
        validation=ValidationPeriod(start + timedelta(days=24), start + timedelta(days=47)),
        out_of_sample=ValidationPeriod(start + timedelta(days=48), start + timedelta(days=71)),
    )
    evidence = ResearchValidationEvidence(
        dataset_version_hashes=(_hash("dataset-version"),),
        feature_version_hashes=(_hash("feature-version"),),
        strategy_version_hash=_hash("strategy-version"),
        experiment_spec_hash=_hash("experiment-spec"),
        experiment_run_hash=_hash("experiment-run"),
        backtest_result_hash=manifest.result.result_hash,
        input_kind=ResearchInputEvidenceKind.DATASET_VERSIONED,
        fixture_replay_binding_hash=None,
        code_revision="research-card-test",
    )
    report = evaluate_validation(
        evidence=evidence,
        series=series,
        split=split,
        walk_forward_folds=(
            WalkForwardFold(
                fold_id="wf_01",
                split=ValidationSplit(
                    in_sample=ValidationPeriod(start, start + timedelta(days=11)),
                    validation=ValidationPeriod(start + timedelta(days=12), start + timedelta(days=17)),
                    out_of_sample=ValidationPeriod(start + timedelta(days=18), start + timedelta(days=23)),
                ),
            ),
        ),
        rolling_window=RollingWindow(window_sessions=12, stride_sessions=6),
        stress_scenarios=(
            StressScenario("base", StressKind.BASELINE),
            StressScenario("cost", StressKind.TRANSACTION_COST, penalty_bps=2),
        ),
        parameter_neighbors=(
            ParameterNeighbor.create(
                neighbor_id="lookback_19",
                parameters={"lookback": 19},
                series=series,
            ),
        ),
        bootstrap_iterations=10,
        monte_carlo_iterations=10,
        random_seed=7,
    )
    admission = ResearchAdmissionResult(
        policy_id="research-policy",
        policy_config_sha256=_hash("policy"),
        status="PASS",
        eligible_for_human_review=True,
        source_id="fixture-source",
        target_universe_id="cn-futures",
        checks=(),
        summary="review complete",
    )
    decision_evidence = ResearchDecisionEvidence.from_validation_report(
        experiment_spec_hash=evidence.experiment_spec_hash,
        experiment_run_hash=evidence.experiment_run_hash,
        backtest_result_hash=manifest.result.result_hash,
        validation_report=report,
        admission_result=admission,
    )
    decision = ResearchDecision.draft(decision_id="trend-v1").transition(
        target_state=ResearchDecisionState.RESEARCH_ONLY
    ).transition(
        target_state=ResearchDecisionState.CANDIDATE,
        evidence=decision_evidence,
        approval=HumanResearchApproval(
            approval_id="approval-candidate",
            approver_id="research-owner",
            approved_at=datetime(2026, 8, 21, 9, tzinfo=UTC),
            target_state=ResearchDecisionState.CANDIDATE,
            rationale="reviewed",
        ),
    )
    return report, decision


def test_research_card_is_reproducible_and_contains_the_required_research_evidence() -> None:
    manifest = _manifest()
    validation, decision = _validation_and_decision(manifest)
    card = ResearchCard.create(
        card_id="trend-v1-card",
        run_manifest=manifest,
        validation_report=validation,
        decision=decision,
        product_contributions=(ProductContribution("RB", 0.04, 0.2, -0.03),),
        limitations=("weight return is a continuous-return approximation",),
    )
    repeated = ResearchCard.create(
        card_id="trend-v1-card",
        run_manifest=manifest,
        validation_report=validation,
        decision=decision,
        product_contributions=(ProductContribution("RB", 0.04, 0.2, -0.03),),
        limitations=("weight return is a continuous-return approximation",),
    )

    payload = card.as_mapping()
    reproducibility = payload["reproducibility"]
    assert isinstance(reproducibility, dict)
    assert reproducibility["backtest_result_hash"] == manifest.result.result_hash
    assert payload["execution_assumptions"] == {
        "commission_bps": 1.0,
        "min_commission": 2.0,
        "slippage_bps": 3.0,
        "slippage_ticks": 1.0,
    }
    assert payload["backtest_summary"] == {"turnover": 0.3, "max_drawdown": -0.05}
    assert payload["decision"]["state"] == "candidate"
    assert payload["eligible_for_trading"] is False
    assert card.card_hash == repeated.card_hash
    assert card.to_json() == repeated.to_json()


def test_research_card_rejects_validation_from_another_backtest_result() -> None:
    manifest = _manifest()
    validation, decision = _validation_and_decision(manifest)
    mismatched_evidence = replace(validation.evidence, backtest_result_hash=_hash("other-result"))
    mismatched_validation = replace(validation, evidence=mismatched_evidence)

    with pytest.raises(ResearchReportError, match="run manifest result"):
        ResearchCard.create(
            card_id="trend-v1-card",
            run_manifest=manifest,
            validation_report=mismatched_validation,
            decision=decision,
            product_contributions=(ProductContribution("RB", 0.04, 0.2, -0.03),),
            limitations=("weight return is a continuous-return approximation",),
        )
