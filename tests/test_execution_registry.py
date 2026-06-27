from datetime import UTC, datetime

import polars as pl

from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.execution.models import BrokerStateSnapshot, FillSnapshot, PositionSnapshot
from northstar_quant.execution.registry import (
    build_execution_plan,
    project_broker_state_positions,
    resolve_execution_planner,
)


def test_resolve_execution_planner_for_daily_etf_profile():
    profile = load_trading_profile("cn_etf_daily")

    definition = resolve_execution_planner(profile, StrategyOutputType.TARGET_WEIGHT)

    assert definition.planner_id == "bar_close_rebalance"


def test_resolve_execution_planner_for_intraday_equity_profile():
    profile = load_trading_profile("cn_stock_intraday_1m")

    definition = resolve_execution_planner(profile, StrategyOutputType.EXECUTION_INTENT)

    assert definition.planner_id == "direct_execution_intent"


def test_intraday_execution_plan_supports_order_semantics():
    profile = load_trading_profile("cn_stock_intraday_1m")
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
    state = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(symbol="AAPL", qty=0.0),
            PositionSnapshot(symbol="MSFT", qty=100.0),
            PositionSnapshot(symbol="NVDA", qty=400.0),
            PositionSnapshot(symbol="TSLA", qty=20.0),
        ]
    )
    latest_prices = {"AAPL": 100.0, "MSFT": 50.0, "NVDA": 200.0, "TSLA": 1000.0}

    plans = build_execution_plan(
        profile,
        intents,
        StrategyOutputType.EXECUTION_INTENT,
        state,
        latest_prices,
        equity=100000.0,
    )

    plan_by_symbol = {plan.symbol: plan for plan in plans}

    assert set(plan_by_symbol) == {"AAPL", "MSFT", "NVDA", "TSLA"}
    assert plan_by_symbol["AAPL"].order_semantic == "entry"
    assert plan_by_symbol["AAPL"].qty == 100.0
    assert plan_by_symbol["MSFT"].order_semantic == "exit"
    assert plan_by_symbol["MSFT"].qty == 100.0
    assert plan_by_symbol["NVDA"].order_semantic == "reduce"
    assert plan_by_symbol["NVDA"].qty == 100.0
    assert plan_by_symbol["TSLA"].order_semantic == "reverse"
    assert plan_by_symbol["TSLA"].qty == 25.0
    assert plan_by_symbol["TSLA"].reason == "flip_short"


def test_project_broker_state_positions_nets_working_orders_and_recent_fills():
    state = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(symbol="AAPL", qty=100.0),
            PositionSnapshot(symbol="MSFT", qty=0.0),
        ],
        open_orders=[
            {
                "symbol": "AAPL",
                "side": "BUY",
                "qty": 50.0,
                "remaining_qty": 30.0,
                "filled_qty": 20.0,
                "status": "Submitted",
            },
            {
                "symbol": "MSFT",
                "side": "SELL",
                "qty": 40.0,
                "status": "open",
            },
        ],
        fills=[
            FillSnapshot(
                broker_order_id="late-fill",
                symbol="AAPL",
                qty=10.0,
                price=100.0,
                side="BUY",
                filled_at=datetime(2024, 1, 2, 10, 0, 5, tzinfo=UTC),
            ),
            FillSnapshot(
                broker_order_id="old-fill",
                symbol="AAPL",
                qty=99.0,
                price=100.0,
                side="BUY",
                filled_at=datetime(2024, 1, 2, 9, 59, 59, tzinfo=UTC),
            ),
        ],
        asof=datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC),
    )

    projected = project_broker_state_positions(state)
    qty_by_symbol = {row.symbol: row.qty for row in projected}

    assert qty_by_symbol["AAPL"] == 140.0
    assert qty_by_symbol["MSFT"] == -40.0


def test_build_execution_plan_is_idempotent_against_working_orders():
    profile = load_trading_profile("cn_etf_daily")
    targets = pl.DataFrame([{"symbol": "SPY", "target_weight": 0.5}])
    state = BrokerStateSnapshot(
        positions=[PositionSnapshot(symbol="SPY", qty=0.0)],
        open_orders=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 300.0,
                "remaining_qty": 300.0,
                "status": "Submitted",
            }
        ],
    )
    latest_prices = {"SPY": 100.0}

    plans = build_execution_plan(
        profile,
        targets,
        StrategyOutputType.TARGET_WEIGHT,
        state,
        latest_prices,
        equity=100000.0,
    )

    assert len(plans) == 1
    assert plans[0].symbol == "SPY"
    assert plans[0].side == "BUY"
    assert plans[0].qty == 200.0


def test_execution_intent_entry_skips_buy_qty_below_lot_step():
    profile = load_trading_profile("cn_stock_intraday_1m")
    intents = pl.DataFrame(
        [
            {
                "symbol": "AAPL",
                "signal_value": 1.0,
                "side": "BUY",
                "size_fraction": 0.10,
                "order_semantic": "entry",
            }
        ]
    )
    state = BrokerStateSnapshot(
        positions=[PositionSnapshot(symbol="AAPL", qty=0.0)],
        open_orders=[
            {
                "symbol": "AAPL",
                "side": "BUY",
                "qty": 40.0,
                "remaining_qty": 40.0,
                "status": "Submitted",
            }
        ],
    )
    latest_prices = {"AAPL": 100.0}

    plans = build_execution_plan(
        profile,
        intents,
        StrategyOutputType.EXECUTION_INTENT,
        state,
        latest_prices,
        equity=100000.0,
    )

    assert plans == []
