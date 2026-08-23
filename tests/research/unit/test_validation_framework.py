"""P2-WP06：确定性 IS/OOS、压力与稳健性验证。"""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256

import pytest

from northstar_quant.research.validation.framework import (
    ReturnObservation,
    ParameterNeighbor,
    ResearchInputEvidenceKind,
    ResearchValidationEvidence,
    RollingWindow,
    StressKind,
    StressScenario,
    ValidationError,
    ValidationPeriod,
    ValidationReturnSeries,
    ValidationSplit,
    WalkForwardFold,
    evaluate_validation,
)


def _series(*, oos_regime: str | None = "bull") -> ValidationReturnSeries:
    start = date(2020, 1, 1)
    return ValidationReturnSeries.create(
        ReturnObservation(
            session=start + timedelta(days=index),
            net_return=(0.002 if index % 3 else -0.001),
            regime=("bear" if index < 40 else oos_regime),
        )
        for index in range(60)
    )


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _evidence() -> ResearchValidationEvidence:
    return ResearchValidationEvidence(
        dataset_version_hashes=(_hash("dataset"),),
        feature_version_hashes=(_hash("feature"),),
        strategy_version_hash=_hash("strategy"),
        experiment_spec_hash=_hash("experiment-spec"),
        experiment_run_hash=_hash("experiment-run"),
        backtest_result_hash=_hash("result"),
        input_kind=ResearchInputEvidenceKind.DATASET_VERSIONED,
        fixture_replay_binding_hash=None,
        code_revision="p2-wp06-test",
    )


def _split() -> ValidationSplit:
    return ValidationSplit(
        in_sample=ValidationPeriod(date(2020, 1, 1), date(2020, 1, 20)),
        validation=ValidationPeriod(date(2020, 1, 21), date(2020, 2, 9)),
        out_of_sample=ValidationPeriod(date(2020, 2, 10), date(2020, 2, 29)),
    )


def _folds() -> tuple[WalkForwardFold, ...]:
    return (
        WalkForwardFold(
            fold_id="fold_01",
            split=ValidationSplit(
                in_sample=ValidationPeriod(date(2020, 1, 1), date(2020, 1, 8)),
                validation=ValidationPeriod(date(2020, 1, 9), date(2020, 1, 12)),
                out_of_sample=ValidationPeriod(date(2020, 1, 13), date(2020, 1, 16)),
            ),
        ),
        WalkForwardFold(
            fold_id="fold_02",
            split=ValidationSplit(
                in_sample=ValidationPeriod(date(2020, 1, 17), date(2020, 1, 24)),
                validation=ValidationPeriod(date(2020, 1, 25), date(2020, 1, 28)),
                out_of_sample=ValidationPeriod(date(2020, 1, 29), date(2020, 2, 1)),
            ),
        ),
    )


def _scenarios() -> tuple[StressScenario, ...]:
    return (
        StressScenario("baseline", StressKind.BASELINE),
        StressScenario("cost_x2", StressKind.TRANSACTION_COST, penalty_bps=2.0),
        StressScenario("slippage_x3", StressKind.SLIPPAGE, penalty_bps=3.0),
        StressScenario("latency_1", StressKind.LATENCY, delay_sessions=1),
    )


def test_full_validation_is_deterministic_and_keeps_all_required_evidence() -> None:
    series = _series()
    kwargs = {
        "evidence": _evidence(),
        "series": series,
        "split": _split(),
        "walk_forward_folds": _folds(),
        "rolling_window": RollingWindow(window_sessions=10, stride_sessions=5),
        "stress_scenarios": _scenarios(),
        "parameter_neighbors": (
            ParameterNeighbor.create(
                neighbor_id="neighbor_down",
                parameters={"lookback": 19},
                series=series,
            ),
            ParameterNeighbor.create(
                neighbor_id="neighbor_up",
                parameters={"lookback": 21},
                series=series,
            ),
        ),
        "bootstrap_iterations": 20,
        "monte_carlo_iterations": 20,
        "random_seed": 17,
    }

    report = evaluate_validation(**kwargs)
    repeated = evaluate_validation(**kwargs)

    assert report.report_hash == repeated.report_hash
    assert report.input_series_hash == series.series_hash
    assert len(report.walk_forward_oos_metrics) == 2
    assert len(report.rolling_metrics) == 11
    assert {name for name, _ in report.stress_metrics} == {
        "baseline",
        "cost_x2",
        "slippage_x3",
        "latency_1",
    }
    assert report.bootstrap.total_return_p05 <= report.bootstrap.total_return_p95
    assert report.monte_carlo.total_return_p05 <= report.monte_carlo.total_return_p95
    assert report.as_mapping()["input_series_hash"] == series.series_hash


def test_validation_fails_closed_for_missing_oos_regime_or_baseline_scenario() -> None:
    with pytest.raises(ValidationError, match="regime"):
        evaluate_validation(
            evidence=_evidence(),
            series=_series(oos_regime=None),
            split=_split(),
            walk_forward_folds=_folds(),
            rolling_window=RollingWindow(window_sessions=10, stride_sessions=5),
            stress_scenarios=_scenarios(),
            parameter_neighbors=(
                ParameterNeighbor.create(
                    neighbor_id="neighbor",
                    parameters={"lookback": 20},
                    series=_series(),
                ),
            ),
            bootstrap_iterations=10,
            monte_carlo_iterations=10,
            random_seed=0,
        )
    with pytest.raises(ValidationError, match="baseline"):
        evaluate_validation(
            evidence=_evidence(),
            series=_series(),
            split=_split(),
            walk_forward_folds=_folds(),
            rolling_window=RollingWindow(window_sessions=10, stride_sessions=5),
            stress_scenarios=(StressScenario("cost", StressKind.TRANSACTION_COST, penalty_bps=1),),
            parameter_neighbors=(
                ParameterNeighbor.create(
                    neighbor_id="neighbor",
                    parameters={"lookback": 20},
                    series=_series(),
                ),
            ),
            bootstrap_iterations=10,
            monte_carlo_iterations=10,
            random_seed=0,
        )


def test_validation_rejects_overlapping_walk_forward_oos_folds() -> None:
    overlapping = (
        _folds()[0],
        WalkForwardFold(
            fold_id="fold_overlap",
            split=ValidationSplit(
                in_sample=ValidationPeriod(date(2020, 1, 1), date(2020, 1, 8)),
                validation=ValidationPeriod(date(2020, 1, 9), date(2020, 1, 15)),
                out_of_sample=ValidationPeriod(date(2020, 1, 16), date(2020, 1, 23)),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="OOS folds"):
        evaluate_validation(
            evidence=_evidence(),
            series=_series(),
            split=_split(),
            walk_forward_folds=overlapping,
            rolling_window=RollingWindow(window_sessions=10, stride_sessions=5),
            stress_scenarios=_scenarios(),
            parameter_neighbors=(
                ParameterNeighbor.create(
                    neighbor_id="neighbor",
                    parameters={"lookback": 20},
                    series=_series(),
                ),
            ),
            bootstrap_iterations=10,
            monte_carlo_iterations=10,
            random_seed=0,
        )
