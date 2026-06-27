import polars as pl

from northstar_quant.execution.models import PositionSnapshot
from northstar_quant.execution.rebalance import build_rebalance_plan


def test_build_rebalance_plan_creates_orders():
    targets = pl.DataFrame([
        {"symbol": "SPY", "target_weight": 0.5},
        {"symbol": "QQQ", "target_weight": 0.5},
    ])
    positions = [PositionSnapshot(symbol="SPY", qty=0)]
    prices = {"SPY": 500.0, "QQQ": 400.0}

    plans = build_rebalance_plan(targets, positions, prices, equity=100000)
    assert len(plans) >= 2


def test_build_rebalance_plan_respects_weight_tolerance_and_long_only():
    targets = pl.DataFrame(
        [
            {"symbol": "SPY", "target_weight": 0.49},
            {"symbol": "QQQ", "target_weight": -0.10},
        ]
    )
    positions = [
        PositionSnapshot(symbol="SPY", qty=100.0),
        PositionSnapshot(symbol="QQQ", qty=0.0),
    ]
    prices = {"SPY": 500.0, "QQQ": 400.0}

    plans = build_rebalance_plan(
        targets,
        positions,
        prices,
        equity=100000,
        rebalance_weight_tolerance=0.02,
        long_only=True,
    )

    assert plans == []
