"""兼容旧入口的日频/周频仿真回测器。"""

from __future__ import annotations

from northstar_quant.backtest.canonical import run_strategy_output_backtest
from northstar_quant.backtest.simulation import BarSimulationBacktesterBase
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.storage import load_profile_signal_data
from northstar_quant.strategies.pipeline import (
    parse_strategy_selection,
    resolve_selected_profile_strategy_ids,
    run_profile_strategy_pipeline,
)


class BacktraderBarSimulationBacktester(BarSimulationBacktesterBase):
    """基于 canonical profile pipeline 的条形仿真回测器。"""

    backtester_id = "backtrader_bar_simulation"

    def run(
        self,
        profile,
        *,
        strategy_name: str,
        symbol: str = "510300.SS",
    ) -> dict:
        del symbol
        strategy_ids = parse_strategy_selection(strategy_name)
        selected_strategy_ids = resolve_selected_profile_strategy_ids(
            profile,
            strategy_ids=strategy_ids,
        )
        market_df = load_profile_signal_data(profile.profile_id)
        pipeline = run_profile_strategy_pipeline(
            market_df,
            profile,
            strategy_ids=strategy_ids,
            latest_only=False,
        )
        result = run_strategy_output_backtest(profile, market_df, pipeline)

        return {
            "backtester": self.backtester_id,
            "strategy": strategy_name,
            "selected_strategy_ids": list(selected_strategy_ids),
            "output_type": pipeline.output_type.value,
            "total_return": result.total_return,
            "annualized_return": result.annualized_return,
            "max_drawdown": result.max_drawdown,
            "turnover_estimate": result.turnover_estimate,
            "bars": len(result.equity_curve),
        }


def run_backtrader_demo(
    symbol: str = "510300.SS",
    strategy_name: str = "portfolio",
    profile_id: str | None = None,
) -> dict:
    """兼容旧入口的 profile 仿真回测函数。"""

    profile = load_trading_profile(profile_id)
    return BacktraderBarSimulationBacktester().run(
        profile,
        strategy_name=strategy_name,
        symbol=symbol,
    )
