"""回测运行层的绩效和基准口径测试。"""

from __future__ import annotations

from datetime import date

import pytest

from northstar_quant.application.backtest import (
    _build_evaluation_view,
    _build_benchmark_analytics,
    _calculate_equity_performance,
)
from northstar_quant.research.backtest.models import BacktestEngine, BacktestResult


def test_equity_performance_includes_initial_equity_for_first_day_loss():
    performance = _calculate_equity_performance(
        [
            {"date": "2024-01-02", "equity": 0.95},
            {"date": "2024-01-03", "equity": 1.045},
        ],
        periods_per_year=252,
    )

    assert performance["total_return"] == pytest.approx(0.045)
    assert performance["max_drawdown"] == pytest.approx(-0.05)
    assert performance["return_observation_count"] == 2


def test_equity_performance_returns_na_risk_ratios_when_one_period_has_no_dispersion():
    performance = _calculate_equity_performance(
        [{"date": "2024-01-02", "equity": 1.01}],
        periods_per_year=252,
    )

    assert performance["annualized_volatility"] is None
    assert performance["annualized_return"] is None
    assert performance["sharpe_ratio"] is None
    assert performance["sortino_ratio"] is None
    assert performance["calmar_ratio"] is None
    assert performance["sample_is_sufficient"] is False


def test_evaluation_view_excludes_signal_warmup_before_first_executable_target():
    import polars as pl

    result = BacktestResult(
        engine=BacktestEngine.WEIGHT_RETURN,
        total_return=0.1,
        annualized_return=0.1,
        max_drawdown=0.0,
        turnover_estimate=0.0,
        equity_curve=[
            {"date": "2024-01-02", "equity": 1.0},
            {"date": "2024-01-03", "equity": 1.0},
            {"date": "2024-01-04", "equity": 1.0},
            {"date": "2024-01-05", "equity": 1.1},
        ],
    )
    evaluation = _build_evaluation_view(
        result,
        targets=pl.DataFrame(
            {
                "date": [
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                ],
                "symbol": ["RB_CONT", "RB_CONT", "RB_CONT"],
                "target_weight": [0.0, 0.0, 0.2],
            }
        ),
        time_column="date",
        execution_delay_sessions=1,
    )

    assert evaluation["metadata"]["evaluation_start"] == "2024-01-05"
    assert evaluation["metadata"]["warmup_excluded_observation_count"] == 3
    assert evaluation["equity_curve"] == [{"date": "2024-01-05", "equity": 1.1}]


def test_benchmark_is_explicitly_unavailable_when_curve_dates_are_missing():
    import polars as pl

    benchmark = _build_benchmark_analytics(
        pl.DataFrame(
            {
                "date": ["2024-01-02"],
                "symbol": ["RB_CONT"],
                "close": [100.0],
            }
        ).with_columns(pl.col("date").str.to_date()),
        benchmark_symbol="RB_CONT",
        equity_curve=[
            {"date": "2024-01-02", "equity": 1.0},
            {"date": "2024-01-03", "equity": 1.01},
        ],
        periods_per_year=252,
    )

    assert benchmark["status"] == "unavailable"
    assert "缺少基准价格" in str(benchmark["reason"])
