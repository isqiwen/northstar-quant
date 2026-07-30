"""实际合约目标权重适配器测试。"""

import polars as pl

from northstar_quant.backtest.futures_actual_adapter import (
    run_actual_futures_backtest,
)
from northstar_quant.config.trading_profile import load_trading_profile
from tests.support.futures_actual import actual_futures_frame


def test_actual_adapter_maps_continuous_target_to_contract_and_rolls_position():
    profile = load_trading_profile("cn_futures_daily_actual_offline")
    market = actual_futures_frame()
    days = sorted(market.get_column("date").unique().to_list())
    targets = pl.DataFrame(
        {
            "date": [days[0]],
            "symbol": ["RB_CONT"],
            "target_weight": [0.1],
        }
    )

    result = run_actual_futures_backtest(profile, market, targets)

    reasons = [trade["reason"] for trade in result.trades]
    assert "target_open" in reasons
    assert "roll_close" in reasons
    assert "roll_open" in reasons
    assert all(not str(trade["instrument_id"]).endswith("_CONT") for trade in result.trades)
    assert all("margin" in row and "available_funds" in row for row in result.equity_curve)
    assert result.equity_curve[-1]["equity"] > 0
