"""分钟级仿真回测器。"""

from __future__ import annotations

from northstar_quant.backtest.intent_event_engine import run_execution_intent_backtest
from northstar_quant.backtest.intraday_event_engine import run_intraday_event_backtest
from northstar_quant.backtest.simulation import (
    MinuteSimulationBacktesterBase,
    periods_per_year_for_frequency,
)
from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.data.storage import load_profile_signal_data
from northstar_quant.strategies.base import IntradayStrategyBase
from northstar_quant.strategies.registry import build_strategy


class IntradaySignalSimulationBacktester(MinuteSimulationBacktesterBase):
    """分钟级仿真回测器。"""

    backtester_id = "intraday_signal_simulation"

    def run(
        self,
        profile,
        *,
        strategy_name: str,
        symbol: str = "SPY",
    ) -> dict:
        market_df = load_profile_signal_data(profile.profile_id)
        strategy = build_strategy(strategy_name)
        if not isinstance(strategy, IntradayStrategyBase):
            raise ValueError(
                f"分钟级仿真回测器仅支持盘中策略，当前策略 {strategy_name} 不属于 IntradayStrategyBase"
            )

        output = strategy.generate_output(market_df)
        periods_per_year = periods_per_year_for_frequency(profile.data_frequency)
        if strategy.output_type == StrategyOutputType.EXECUTION_INTENT:
            result = run_execution_intent_backtest(
                market_df,
                output,
                periods_per_year=periods_per_year,
            )
        else:
            result = run_intraday_event_backtest(
                market_df,
                output,
                periods_per_year=periods_per_year,
            )

        return {
            "backtester": self.backtester_id,
            "strategy": strategy_name,
            "symbol": symbol,
            "output_type": strategy.output_type.value,
            "total_return": result.total_return,
            "annualized_return": result.annualized_return,
            "max_drawdown": result.max_drawdown,
            "turnover_estimate": result.turnover_estimate,
            "bars": len(result.equity_curve),
        }
