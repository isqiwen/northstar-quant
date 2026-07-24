"""Canonical backtest helpers built on shared strategy outputs."""

from __future__ import annotations

from northstar_quant.backtest.event_engine import BacktestResult
from northstar_quant.backtest.intent_event_engine import run_execution_intent_backtest
from northstar_quant.backtest.intraday_event_engine import run_intraday_event_backtest
from northstar_quant.backtest.registry import run_target_backtest
from northstar_quant.backtest.simulation import periods_per_year_for_frequency
from northstar_quant.common.enums import DataFrequency, StrategyOutputType
from northstar_quant.common.types import StrategyOutputBundle
from northstar_quant.config.trading_profile import TradingProfile


def run_strategy_output_backtest(
    profile: TradingProfile,
    market_df,
    output_bundle: StrategyOutputBundle,
) -> BacktestResult:
    """Run the appropriate backtest engine for a canonical strategy output bundle."""

    periods_per_year = periods_per_year_for_frequency(profile.data_frequency)
    if output_bundle.output_type == StrategyOutputType.TARGET_WEIGHT:
        if profile.data_frequency in {DataFrequency.D1, DataFrequency.W1}:
            return run_target_backtest(profile, market_df, output_bundle.frame)
        return run_intraday_event_backtest(
            market_df,
            output_bundle.frame,
            periods_per_year=periods_per_year,
        )

    if output_bundle.output_type == StrategyOutputType.EXECUTION_INTENT:
        return run_execution_intent_backtest(
            market_df,
            output_bundle.frame,
            periods_per_year=periods_per_year,
        )

    if output_bundle.output_type == StrategyOutputType.TRADE_PLAN:
        raise ValueError(
            "TradePlan 策略尚未接入专用回测状态机，不能错误地按目标权重或订单意图回测。"
        )

    raise ValueError(
        f"暂不支持的策略输出类型：{output_bundle.output_type.value}"
    )
