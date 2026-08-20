"""实际合约目标权重适配器测试。"""

import polars as pl
import pytest

from northstar_quant.research.backtest.futures_actual_adapter import (
    _to_backtest_result,
    run_actual_futures_backtest,
)
from northstar_quant.research.backtest.futures_daily import FuturesDailyBacktestResult
from northstar_quant.platform.config.trading_profile import load_trading_profile
from tests.helpers.futures_actual import actual_futures_frame


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
    assert all(trade["notional"] > 0 for trade in result.trades)
    assert all(
        "margin" in row
        and "available_funds" in row
        and "margin_ratio" in row
        and "available_funds_ratio" in row
        for row in result.equity_curve
    )
    assert result.equity_curve[-1]["equity"] > 0


def test_actual_adapter_closes_position_for_explicit_zero_target_weight():
    profile = load_trading_profile("cn_futures_daily_actual_offline")
    market = actual_futures_frame()
    days = sorted(market.get_column("date").unique().to_list())
    targets = pl.DataFrame(
        {
            "date": [days[0], days[1]],
            "symbol": ["RB_CONT", "RB_CONT"],
            "target_weight": [0.1, 0.0],
        }
    )

    result = run_actual_futures_backtest(profile, market, targets)

    assert [trade["reason"] for trade in result.trades] == [
        "target_open",
        "target_reduce",
    ]


def test_actual_adapter_drawdown_uses_initial_equity_as_first_peak():
    state_result = FuturesDailyBacktestResult(
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

    result = _to_backtest_result(
        state_result,
        {},
        initial_cash=100_000.0,
        periods_per_year=252,
    )

    assert result.max_drawdown == pytest.approx(-0.1)
    assert [row["drawdown"] for row in result.drawdown_curve] == pytest.approx(
        [-0.1, -0.05]
    )
