import math
from dataclasses import replace
from datetime import datetime, timedelta

import polars as pl
import pytest

from northstar_quant.backtest import registry as backtest_registry
from northstar_quant.backtest.registry import (
    resolve_simulation_backtester,
    resolve_target_backtester,
    run_simulation_backtest,
    run_target_backtest,
)
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.downloader import download_profile_data


def test_resolve_target_backtester_for_daily_profile():
    profile = load_trading_profile("cn_etf_daily")

    definition = resolve_target_backtester(profile)

    assert definition.backtester_id == "bar_event_backtest"


def test_resolve_target_backtester_for_intraday_profile():
    profile = load_trading_profile("cn_stock_intraday_1m")

    definition = resolve_target_backtester(profile)

    assert definition.backtester_id == "intraday_event_backtest"


def test_resolve_target_backtester_for_stateful_paper_profile():
    profile = load_trading_profile("cn_etf_daily_paper")

    definition = resolve_target_backtester(profile)

    assert definition.backtester_id == "daily_stateful_backtest"


def test_run_target_backtest_uses_profile_stateful_cost_model():
    profile = load_trading_profile("cn_etf_daily_paper")
    market_df = pl.DataFrame(
        [
            {"date": datetime(2024, 1, 2), "symbol": "AAA", "open": 10.0, "close": 10.0},
            {"date": datetime(2024, 1, 3), "symbol": "AAA", "open": 10.0, "close": 10.0},
        ]
    )
    targets = pl.DataFrame(
        [
            {
                "date": datetime(2024, 1, 2),
                "symbol": "AAA",
                "target_weight": 1.0,
            }
        ]
    )

    result = run_target_backtest(profile, market_df, targets)

    # 100 股整数倍、滑点和最低佣金使成交成本严格大于零。
    assert result.total_return < 0
    assert result.turnover_estimate > 0


def test_resolve_simulation_backtester_for_daily_stock_profile():
    profile = load_trading_profile("cn_stock_daily")

    definition = resolve_simulation_backtester(profile)

    assert definition.backtester_id == "backtrader_bar_simulation"


def test_resolve_simulation_backtester_for_intraday_profile():
    profile = load_trading_profile("cn_stock_intraday_1m")

    definition = resolve_simulation_backtester(profile)

    assert definition.backtester_id == "intraday_signal_simulation"


def test_resolve_target_backtester_rejects_ambiguous_match(monkeypatch):
    profile = load_trading_profile("cn_etf_daily")
    definition = resolve_target_backtester(profile)
    duplicate_id = "duplicate_bar_event_backtest"
    monkeypatch.setitem(
        backtest_registry._TARGET_BACKTESTERS,
        duplicate_id,
        replace(definition, backtester_id=duplicate_id),
    )

    with pytest.raises(LookupError, match="匹配到多个目标持仓回测器"):
        resolve_target_backtester(profile)


def test_resolve_simulation_backtester_rejects_ambiguous_match(monkeypatch):
    profile = load_trading_profile("cn_stock_daily")
    definition = resolve_simulation_backtester(profile)
    duplicate_id = "duplicate_backtrader_bar_simulation"
    monkeypatch.setitem(
        backtest_registry._SIMULATION_BACKTESTERS,
        duplicate_id,
        replace(definition, backtester_id=duplicate_id),
    )

    with pytest.raises(LookupError, match="匹配到多个策略仿真回测器"):
        resolve_simulation_backtester(profile)


def test_run_target_backtest_uses_intraday_engine_for_intraday_profile():
    profile = load_trading_profile("cn_stock_intraday_1m")
    start = datetime(2024, 3, 4, 9, 30)

    market_rows: list[dict] = []
    target_rows: list[dict] = []
    aapl_price = 100.0
    msft_price = 200.0

    for offset in range(10):
        timestamp = start + timedelta(minutes=offset)
        aapl_price += 0.3
        msft_price += 0.1
        market_rows.extend(
            [
                {"timestamp": timestamp, "symbol": "AAPL", "close": aapl_price},
                {"timestamp": timestamp, "symbol": "MSFT", "close": msft_price},
            ]
        )
        target_rows.extend(
            [
                {
                    "timestamp": timestamp,
                    "symbol": "AAPL",
                    "signal_value": 1.0,
                    "target_weight": 0.6,
                },
                {
                    "timestamp": timestamp,
                    "symbol": "MSFT",
                    "signal_value": 1.0,
                    "target_weight": 0.4,
                },
            ]
        )

    result = run_target_backtest(profile, pl.DataFrame(market_rows), pl.DataFrame(target_rows))

    assert result.total_return > 0
    assert result.annualized_return > 0
    assert len(result.equity_curve) == 10


def test_run_simulation_backtest_uses_intraday_simulator_for_intraday_profile():
    profile = load_trading_profile("cn_stock_intraday_1m")
    download_profile_data(profile.profile_id, provider_override="demo")

    result = run_simulation_backtest(profile, strategy_name="intraday_breakout")

    assert result["backtester"] == "intraday_signal_simulation"
    assert result["strategy"] == "intraday_breakout"
    assert result["selected_strategy_ids"] == ["intraday_breakout"]
    assert result["output_type"] == "execution_intent"
    assert math.isfinite(result["annualized_return"])
    assert result["bars"] > 0
