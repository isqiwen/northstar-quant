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


def test_build_rebalance_plan_rounds_buy_qty_down_to_step():
    targets = pl.DataFrame([{"symbol": "510300.SS", "target_weight": 0.123}])
    positions = [PositionSnapshot(symbol="510300.SS", qty=0.0)]
    prices = {"510300.SS": 50.0}

    plans = build_rebalance_plan(
        targets,
        positions,
        prices,
        equity=100000,
        rebalance_min_trade_value=0,
        buy_qty_step=100,
    )

    assert len(plans) == 1
    assert plans[0].side == "BUY"
    assert plans[0].target_qty == 246.0
    assert plans[0].qty == 200.0
    assert plans[0].estimated_trade_value == 10000.0


def test_build_rebalance_plan_keeps_odd_lot_sell_when_sell_step_is_not_set():
    targets = pl.DataFrame([{"symbol": "510300.SS", "target_weight": 0.0}])
    positions = [PositionSnapshot(symbol="510300.SS", qty=105.0)]
    prices = {"510300.SS": 50.0}

    plans = build_rebalance_plan(
        targets,
        positions,
        prices,
        equity=100000,
        rebalance_min_trade_value=0,
        buy_qty_step=100,
    )

    assert len(plans) == 1
    assert plans[0].side == "SELL"
    assert plans[0].qty == 105.0
