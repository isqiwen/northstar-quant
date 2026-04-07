"""分钟级盘中突破示例策略。"""

from __future__ import annotations

import polars as pl

from northstar_quant.strategies.base import MinuteStrategyBase


class IntradayBreakoutStrategy(MinuteStrategyBase):
    """基于滚动高点突破的分钟级多标的执行意图策略。"""

    strategy_id = "intraday_breakout"

    def __init__(
        self,
        lookback_bars: int = 30,
        top_n: int = 2,
        min_breakout_return: float = 0.001,
        order_type: str = "MKT",
    ) -> None:
        self.lookback_bars = lookback_bars
        self.top_n = top_n
        self.min_breakout_return = min_breakout_return
        self.order_type = order_type

    def generate_execution_intents(self, market_df: pl.DataFrame) -> pl.DataFrame:
        self.validate_market_data(market_df)
        close_wide = (
            market_df.pivot(index="timestamp", on="symbol", values="close")
            .sort("timestamp")
        )
        pdf = close_wide.to_pandas().set_index("timestamp")
        rolling_high = pdf.shift(1).rolling(self.lookback_bars, min_periods=self.lookback_bars).max()
        breakout_strength = pdf / rolling_high - 1.0

        rows = []
        for timestamp, row in breakout_strength.dropna(how="all").iterrows():
            ranked = row[row > self.min_breakout_return].sort_values(ascending=False).head(self.top_n)
            if ranked.empty:
                continue
            size_fraction = 1.0 / float(len(ranked))
            for symbol, value in ranked.items():
                rows.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "signal_value": float(value),
                        "side": "BUY",
                        "size_fraction": float(size_fraction),
                        "order_semantic": "entry",
                        "order_type": self.order_type,
                        "reason": "breakout_entry",
                    }
                )
        return self.to_output_frame(rows)
