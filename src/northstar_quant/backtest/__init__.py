"""国内期货研究回测模块。"""

from northstar_quant.backtest.event_engine import BacktestResult, run_event_backtest
from northstar_quant.backtest.futures_daily import (
    FuturesDailyBacktestResult,
    FuturesDailyBar,
    FuturesInstrumentSpec,
    FuturesRollover,
    FuturesTarget,
    FuturesWeightTarget,
    run_daily_futures_backtest,
)
from northstar_quant.backtest.futures_intraday import (
    FuturesIntradayBar,
    FuturesReplayResult,
    IntradayWeightTarget,
    OrderOffset,
    OrderSide,
    OrderStatus,
    OrderType,
    ReplayCancellation,
    ReplayOrderRequest,
    run_intraday_futures_replay,
)

__all__ = [
    "BacktestResult",
    "FuturesDailyBacktestResult",
    "FuturesDailyBar",
    "FuturesInstrumentSpec",
    "FuturesIntradayBar",
    "FuturesReplayResult",
    "FuturesRollover",
    "FuturesTarget",
    "FuturesWeightTarget",
    "IntradayWeightTarget",
    "OrderOffset",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "ReplayCancellation",
    "ReplayOrderRequest",
    "run_daily_futures_backtest",
    "run_event_backtest",
    "run_intraday_futures_replay",
]
