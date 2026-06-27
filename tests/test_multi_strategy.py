import polars as pl
import pytest

from northstar_quant.portfolio.multi_strategy import (
    build_target_weight_portfolio,
    combine_strategy_targets,
)
from northstar_quant.risk.models import RiskLimits


def _weights_by_symbol(targets: pl.DataFrame) -> dict[str, float]:
    return {
        row["symbol"]: float(row["target_weight"])
        for row in targets.select(["symbol", "target_weight"]).to_dicts()
    }


def test_combine_strategy_targets_normalizes_each_strategy_before_scaling():
    strategy_frames = [
        pl.DataFrame(
            [
                {"symbol": "AAA", "target_weight": 2.0},
                {"symbol": "BBB", "target_weight": 1.0},
            ]
        ),
        pl.DataFrame(
            [
                {"symbol": "BBB", "target_weight": 3.0},
            ]
        ),
    ]

    combined = combine_strategy_targets(strategy_frames, [0.6, 0.4])
    weights = _weights_by_symbol(combined)

    assert weights["AAA"] == pytest.approx(0.4)
    assert weights["BBB"] == pytest.approx(0.6)
    assert float(combined["target_weight"].sum()) == pytest.approx(1.0)


def test_build_target_weight_portfolio_preserves_cash_after_risk_constraints():
    strategy_frames = [
        pl.DataFrame(
            [
                {"symbol": "AAA", "target_weight": 0.5},
                {"symbol": "BBB", "target_weight": 0.5},
            ]
        )
    ]
    limits = RiskLimits(
        max_single_weight=0.35,
        max_gross_exposure=1.0,
        min_cash_buffer=0.02,
    )

    combined = build_target_weight_portfolio(strategy_frames, [1.0], limits)
    weights = _weights_by_symbol(combined)

    assert weights["AAA"] == pytest.approx(0.35)
    assert weights["BBB"] == pytest.approx(0.35)
    assert float(combined["target_weight"].max()) == pytest.approx(0.35)
    assert float(combined["target_weight"].sum()) == pytest.approx(0.7)
