"""国内期货研究回测模块。"""

from northstar_quant.backtest.event_engine import BacktestResult, run_event_backtest
from northstar_quant.backtest.futures_daily import (
    FuturesDailyBacktestResult,
    FuturesDailyBar,
    FuturesInstrumentSpec,
    FuturesRollover,
    FuturesTarget,
    run_daily_futures_backtest,
)

__all__ = [
    "BacktestResult",
    "FuturesDailyBacktestResult",
    "FuturesDailyBar",
    "FuturesInstrumentSpec",
    "FuturesRollover",
    "FuturesTarget",
    "run_daily_futures_backtest",
    "run_event_backtest",
]
