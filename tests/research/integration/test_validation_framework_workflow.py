"""P2-WP06 端到端的纯离线验证工作流。"""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256

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


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def test_reproducible_validation_workflow_stays_research_only() -> None:
    start = date(2021, 1, 1)
    series = ValidationReturnSeries.create(
        ReturnObservation(
            session=start + timedelta(days=index),
            net_return=(0.003 if index % 4 else -0.001),
            regime="risk_on" if index % 2 else "risk_off",
        )
        for index in range(72)
    )
    split = ValidationSplit(
        in_sample=ValidationPeriod(start, start + timedelta(days=23)),
        validation=ValidationPeriod(start + timedelta(days=24), start + timedelta(days=47)),
        out_of_sample=ValidationPeriod(start + timedelta(days=48), start + timedelta(days=71)),
    )
    report = evaluate_validation(
        evidence=ResearchValidationEvidence(
            dataset_version_hashes=(_hash("dataset"),),
            feature_version_hashes=(_hash("feature"),),
            strategy_version_hash=_hash("strategy"),
            experiment_spec_hash=_hash("experiment-spec"),
            experiment_run_hash=_hash("experiment-run"),
            backtest_result_hash=_hash("backtest-result"),
            input_kind=ResearchInputEvidenceKind.DATASET_VERSIONED,
            fixture_replay_binding_hash=None,
            code_revision="p2-wp06-integration",
        ),
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
            StressScenario("slip", StressKind.SLIPPAGE, penalty_bps=2),
            StressScenario("delay", StressKind.LATENCY, delay_sessions=1),
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

    assert report.evidence.backtest_result_hash == _hash("backtest-result")
    assert report.eligible_for_admission is False
    assert report.as_mapping()["eligible_for_admission"] is False
