"""分钟实际合约画像适配器测试。"""

import polars as pl
import pytest

from northstar_quant.research.backtest.futures_intraday_adapter import (
    _to_backtest_result,
    run_actual_futures_intraday_replay,
)
from northstar_quant.research.backtest.futures_intraday import FuturesReplayResult
from northstar_quant.platform.config.trading_profile import load_trading_profile
from tests.helpers.futures_actual import actual_futures_intraday_frame


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
    assert all(trade["notional"] > 0 for trade in result.trades)
    assert all("margin_ratio" in row for row in result.equity_curve)
    assert len(result.equity_curve) == 8


def test_intraday_adapter_drawdown_uses_initial_equity_as_first_peak():
    replay = FuturesReplayResult(
        final_equity=95_000.0,
        equity_curve=[
            {
                "date": "2024-01-02",
                "equity": 90_000.0,
                "margin": 0.0,
                "available_funds": 90_000.0,
            },
            {
                "date": "2024-01-03",
                "equity": 95_000.0,
                "margin": 0.0,
                "available_funds": 95_000.0,
            },
        ],
    )

    result = _to_backtest_result(replay, {}, initial_cash=100_000.0)

    assert result.max_drawdown == pytest.approx(-0.1)
    assert [row["drawdown"] for row in result.drawdown_curve] == pytest.approx(
        [-0.1, -0.05]
    )
