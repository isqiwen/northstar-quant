"""分钟实际合约画像适配器测试。"""

import polars as pl

from northstar_quant.backtest.futures_intraday_adapter import (
    run_actual_futures_intraday_replay,
)
from northstar_quant.config.trading_profile import load_trading_profile
from tests.support.futures_actual import actual_futures_intraday_frame


def test_intraday_adapter_maps_daily_signal_and_records_roll_orders():
    profile = load_trading_profile("cn_futures_intraday_replay_offline")
    market = actual_futures_intraday_frame(day_count=8, roll_offset=4)
    days = sorted(market.get_column("date").unique().to_list())
    targets = pl.DataFrame(
        {
            "date": [days[0]],
            "symbol": ["RB_CONT"],
            "target_weight": [0.1],
        }
    )

    result = run_actual_futures_intraday_replay(profile, market, targets)

    reasons = [trade["reason"] for trade in result.trades]
    statuses = {order["status"] for order in result.orders}
    assert "target_open" in reasons
    assert "roll_close" in reasons
    assert "roll_open" in reasons
    assert statuses == {"FILLED"}
    assert all(not str(trade["instrument_id"]).endswith("_CONT") for trade in result.trades)
    assert len(result.equity_curve) == 8
