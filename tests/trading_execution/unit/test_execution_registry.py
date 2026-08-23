"""执行计划注册表的画像执行政策测试。"""

from datetime import UTC, datetime

import pytest
import polars as pl

from northstar_quant.foundation.common.enums import StrategyOutputType
from northstar_quant.foundation.config.trading_profile import load_trading_profile
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    FuturesExecutionRule,
    PositionSnapshot,
    RebalanceOrderPlan,
)
from northstar_quant.trading_execution.execution.registry import (
    _apply_rebalance_tolerance,
    build_execution_plan,
    project_broker_state_positions,
)


def test_rebalance_tolerance_filters_only_provably_small_weight_changes():
    small_change = RebalanceOrderPlan(
        symbol="AAA",
        side="BUY",
        qty=1.0,
        current_qty=10.0,
        target_weight=0.105,
        execution_reference_price=100.0,
    )
    large_change = RebalanceOrderPlan(
        symbol="BBB",
        side="BUY",
        qty=5.0,
        current_qty=10.0,
        target_weight=0.15,
        execution_reference_price=100.0,
    )
    incomplete = RebalanceOrderPlan(
        symbol="CCC",
        side="BUY",
        qty=1.0,
        target_weight=0.1,
    )

    result = _apply_rebalance_tolerance(
        [small_change, large_change, incomplete],
        tolerance=0.01,
        equity=10_000.0,
    )

    assert [plan.symbol for plan in result] == ["BBB", "CCC"]


def test_position_projection_rejects_missing_snapshot_and_fill_timestamps():
    with pytest.raises(ValueError, match="BROKER_STATE_TIMESTAMP_REQUIRED"):
        project_broker_state_positions(BrokerStateSnapshot())

    with pytest.raises(ValueError, match="BROKER_FILL_TIMESTAMP_REQUIRED"):
        project_broker_state_positions(
            BrokerStateSnapshot(
                asof=datetime(2024, 1, 2, tzinfo=UTC),
                fills=[
                    FillSnapshot(
                        broker_order_id="1",
                        symbol="RB2405",
                        qty=1.0,
                        price=100.0,
                        side="BUY",
                    )
                ],
            )
        )


def test_futures_planner_uses_actual_contract_and_splits_shfe_reversal():
    profile = load_trading_profile("cn_futures_daily_trend_simulated")
    state_asof = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)
    output = pl.DataFrame(
        {
            "symbol": ["RB2610"],
            "target_weight": [-0.1],
        }
    )
    state = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(
                symbol="RB2610",
                qty=3.0,
                instrument_id="rb2610",
                exchange_id="SHFE",
                long_today_qty=1.0,
                long_yesterday_qty=2.0,
                short_today_qty=0.0,
                short_yesterday_qty=0.0,
                asof=state_asof,
            )
        ],
        asof=state_asof,
    )

    plans = build_execution_plan(
        profile,
        output,
        StrategyOutputType.TARGET_WEIGHT,
        state,
        {"RB2610": 3100.0},
        equity=100_000.0,
        broker_name="ctp_sim",
        futures_rules={
            "RB2610": FuturesExecutionRule(
                margin_rate=0.1,
                max_position_lots=100,
            )
        },
    )

    assert [
        (plan.symbol, plan.side, plan.qty, plan.ctp_offset, plan.order_semantic)
        for plan in plans
    ] == [
        ("RB2610", "SELL", 2.0, "close_yesterday", "reverse"),
        ("RB2610", "SELL", 1.0, "close_today", "reverse"),
        ("RB2610", "SELL", 3.0, "open", "reverse"),
    ]
    assert all(plan.instrument_id == "rb2610" for plan in plans)
    assert all(plan.exchange_id == "SHFE" for plan in plans)
    assert plans[-1].required_margin == pytest.approx(9300.0)


def test_futures_planner_fails_closed_without_position_buckets():
    profile = load_trading_profile("cn_futures_daily_trend_simulated")
    state_asof = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="CTP_POSITION_DETAIL_REQUIRED"):
        build_execution_plan(
            profile,
            pl.DataFrame(
                {
                    "symbol": ["RB2610"],
                    "target_weight": [0.0],
                }
            ),
            StrategyOutputType.TARGET_WEIGHT,
            BrokerStateSnapshot(
                positions=[PositionSnapshot(symbol="RB2610", qty=1.0)],
                asof=state_asof,
            ),
            {"RB2610": 3100.0},
            equity=100_000.0,
            broker_name="ctp_sim",
            futures_rules={
                "RB2610": FuturesExecutionRule(margin_rate=0.1)
            },
        )


def test_futures_planner_rejects_continuous_research_signal_before_ctp_mapping():
    profile = load_trading_profile("cn_futures_daily_trend_simulated")
    state_asof = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="FUTURES_CONTINUOUS_CONTRACT_FORBIDDEN"):
        build_execution_plan(
            profile,
            pl.DataFrame(
                {
                    "symbol": ["RB_CONT"],
                    "target_weight": [0.1],
                }
            ),
            StrategyOutputType.TARGET_WEIGHT,
            BrokerStateSnapshot(asof=state_asof),
            {"RB2610": 3100.0},
            equity=100_000.0,
            broker_name="ctp_sim",
            futures_rules={"RB2610": FuturesExecutionRule(margin_rate=0.1)},
        )
