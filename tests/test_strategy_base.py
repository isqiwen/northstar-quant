from datetime import datetime

import polars as pl

from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.strategies.intraday_breakout import IntradayBreakoutStrategy
from northstar_quant.strategies.momentum import MomentumRotationStrategy


def test_latest_targets_uses_date_for_date_based_strategy():
    strategy = MomentumRotationStrategy()
    targets = pl.DataFrame(
        [
            {"date": datetime(2024, 1, 1).date(), "symbol": "AAA", "signal_value": 1.0, "target_weight": 0.5},
            {"date": datetime(2024, 1, 1).date(), "symbol": "BBB", "signal_value": 1.0, "target_weight": 0.5},
            {"date": datetime(2024, 1, 2).date(), "symbol": "AAA", "signal_value": 1.2, "target_weight": 1.0},
        ]
    )

    latest = strategy.latest_targets(targets)

    assert latest.height == 1
    assert latest["symbol"].to_list() == ["AAA"]
    assert strategy.output_type == StrategyOutputType.TARGET_WEIGHT


def test_latest_output_uses_timestamp_for_intraday_strategy_and_adds_date():
    strategy = IntradayBreakoutStrategy()
    intents = pl.DataFrame(
        [
            {
                "timestamp": datetime(2024, 3, 4, 9, 35),
                "symbol": "AAPL",
                "signal_value": 0.01,
                "side": "BUY",
                "size_fraction": 0.5,
                "order_semantic": "entry",
            },
            {
                "timestamp": datetime(2024, 3, 4, 9, 35),
                "symbol": "MSFT",
                "signal_value": 0.02,
                "side": "BUY",
                "size_fraction": 0.5,
                "order_semantic": "entry",
            },
            {
                "timestamp": datetime(2024, 3, 4, 9, 40),
                "symbol": "NVDA",
                "signal_value": 0.03,
                "side": "BUY",
                "size_fraction": 1.0,
                "order_semantic": "entry",
            },
        ]
    )

    latest = strategy.latest_output(intents)

    assert latest.height == 1
    assert latest["symbol"].to_list() == ["NVDA"]
    assert "date" in latest.columns
    assert strategy.output_type == StrategyOutputType.EXECUTION_INTENT
