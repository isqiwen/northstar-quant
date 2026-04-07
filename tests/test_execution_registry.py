import polars as pl

from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.execution.models import PositionSnapshot
from northstar_quant.execution.registry import build_execution_plan, resolve_execution_planner


def test_resolve_execution_planner_for_daily_etf_profile():
    profile = load_trading_profile("us_etf_daily")

    definition = resolve_execution_planner(profile, StrategyOutputType.TARGET_WEIGHT)

    assert definition.planner_id == "bar_close_rebalance"


def test_resolve_execution_planner_for_intraday_equity_profile():
    profile = load_trading_profile("us_stock_intraday_1m")

    definition = resolve_execution_planner(profile, StrategyOutputType.EXECUTION_INTENT)

    assert definition.planner_id == "direct_execution_intent"


def test_intraday_execution_plan_supports_order_semantics():
    profile = load_trading_profile("us_stock_intraday_1m")
    intents = pl.DataFrame(
        [
            {
                "symbol": "AAPL",
                "signal_value": 1.0,
                "side": "BUY",
                "size_fraction": 0.10,
                "order_semantic": "entry",
                "reason": "breakout_entry",
            },
            {
                "symbol": "MSFT",
                "signal_value": 0.5,
                "side": "SELL",
                "size_fraction": 0.20,
                "order_semantic": "exit",
                "reason": "risk_exit",
            },
            {
                "symbol": "NVDA",
                "signal_value": 0.4,
                "side": "SELL",
                "size_fraction": 0.25,
                "order_semantic": "reduce",
                "reason": "trim_winner",
            },
            {
                "symbol": "TSLA",
                "signal_value": -0.8,
                "side": "SELL",
                "size_fraction": 0.05,
                "order_semantic": "reverse",
                "reason": "flip_short",
            },
        ]
    )
    positions = [
        PositionSnapshot(symbol="AAPL", qty=0.0),
        PositionSnapshot(symbol="MSFT", qty=10.0),
        PositionSnapshot(symbol="NVDA", qty=40.0),
        PositionSnapshot(symbol="TSLA", qty=20.0),
    ]
    latest_prices = {"AAPL": 100.0, "MSFT": 50.0, "NVDA": 200.0, "TSLA": 1000.0}

    plans = build_execution_plan(
        profile,
        intents,
        StrategyOutputType.EXECUTION_INTENT,
        positions,
        latest_prices,
        equity=100000.0,
    )

    plan_by_symbol = {plan.symbol: plan for plan in plans}

    assert set(plan_by_symbol) == {"AAPL", "MSFT", "NVDA", "TSLA"}
    assert plan_by_symbol["AAPL"].order_semantic == "entry"
    assert plan_by_symbol["AAPL"].qty == 100.0
    assert plan_by_symbol["MSFT"].order_semantic == "exit"
    assert plan_by_symbol["MSFT"].qty == 10.0
    assert plan_by_symbol["NVDA"].order_semantic == "reduce"
    assert plan_by_symbol["NVDA"].qty == 10.0
    assert plan_by_symbol["TSLA"].order_semantic == "reverse"
    assert plan_by_symbol["TSLA"].qty == 25.0
    assert plan_by_symbol["TSLA"].reason == "flip_short"
