"""国内期货实际合约逐日回测公开接口。"""

from northstar_quant.research.backtest.futures_daily.engine import run_daily_futures_backtest
from northstar_quant.research.backtest.futures_daily.models import (
    FuturesDailyBacktestResult,
    FuturesDailyBar,
    FuturesInstrumentSpec,
    FuturesRollover,
    FuturesTarget,
    FuturesTrade,
    FuturesWeightTarget,
)

__all__ = [
    "FuturesDailyBacktestResult",
    "FuturesDailyBar",
    "FuturesInstrumentSpec",
    "FuturesRollover",
    "FuturesTarget",
    "FuturesTrade",
    "FuturesWeightTarget",
    "run_daily_futures_backtest",
]
