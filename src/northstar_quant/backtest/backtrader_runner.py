"""Backtrader 条形仿真回测器。"""

from __future__ import annotations

import backtrader as bt
import pandas as pd

from northstar_quant.backtest.simulation import BarSimulationBacktesterBase
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.storage import load_profile_signal_data


class DualMA(bt.Strategy):
    """双均线示例策略。"""

    params = dict(fast=20, slow=60)

    def __init__(self):
        self.ma_fast = bt.ind.SMA(period=self.params.fast)
        self.ma_slow = bt.ind.SMA(period=self.params.slow)
        self.crossover = bt.ind.CrossOver(self.ma_fast, self.ma_slow)

    def next(self):
        if not self.position and self.crossover > 0:
            self.buy(size=100)
        elif self.position and self.crossover < 0:
            self.close()


class ETFTop1Rotation(bt.Strategy):
    """ETF Top1 轮动示例策略。"""

    params = dict(lookback=60)

    def next(self):
        scores: list[tuple[bt.feeds.PandasData, float]] = []
        for data in self.datas:
            if len(data) <= self.params.lookback:
                continue
            score = data.close[0] / data.close[-self.params.lookback] - 1.0
            scores.append((data, score))

        if not scores:
            return

        winner, _ = sorted(scores, key=lambda item: item[1], reverse=True)[0]

        for data in self.datas:
            if data is winner:
                if not self.getposition(data).size:
                    self.order_target_percent(data=data, target=0.95)
            elif self.getposition(data).size:
                self.order_target_percent(data=data, target=0.0)


class BacktraderBarSimulationBacktester(BarSimulationBacktesterBase):
    """基于 Backtrader 的日频/周频仿真回测器。"""

    backtester_id = "backtrader_bar_simulation"

    def run(
        self,
        profile,
        *,
        strategy_name: str,
        symbol: str = "SPY",
    ) -> dict:
        df = load_profile_signal_data(profile.profile_id)
        cerebro = bt.Cerebro()
        cerebro.broker.setcash(100000.0)
        cerebro.broker.setcommission(commission=0.0005)

        if strategy_name == "momentum":
            pdf = (
                df.filter(df["symbol"] == symbol)
                .select(["date", "open", "high", "low", "close", "volume"])
                .to_pandas()
                .set_index("date")
            )
            pdf.index = pd.to_datetime(pdf.index)
            cerebro.adddata(bt.feeds.PandasData(dataname=pdf), name=symbol)
            cerebro.addstrategy(DualMA)
        elif strategy_name == "etf_rotation":
            for current_symbol in sorted(df["symbol"].unique().to_list()):
                pdf = (
                    df.filter(df["symbol"] == current_symbol)
                    .select(["date", "open", "high", "low", "close", "volume"])
                    .to_pandas()
                    .set_index("date")
                )
                pdf.index = pd.to_datetime(pdf.index)
                cerebro.adddata(bt.feeds.PandasData(dataname=pdf), name=current_symbol)
            cerebro.addstrategy(ETFTop1Rotation)
        else:
            raise ValueError("Backtrader 仿真回测当前仅支持 momentum / etf_rotation")

        start_value = cerebro.broker.getvalue()
        cerebro.run()
        end_value = cerebro.broker.getvalue()

        return {
            "backtester": self.backtester_id,
            "strategy": strategy_name,
            "symbol": symbol,
            "start_value": start_value,
            "end_value": end_value,
            "total_return": end_value / start_value - 1.0,
        }


def run_backtrader_demo(
    symbol: str = "SPY",
    strategy_name: str = "momentum",
    profile_id: str | None = None,
) -> dict:
    """兼容旧入口的 Backtrader 演示回测函数。"""

    profile = load_trading_profile(profile_id)
    return BacktraderBarSimulationBacktester().run(
        profile,
        strategy_name=strategy_name,
        symbol=symbol,
    )
