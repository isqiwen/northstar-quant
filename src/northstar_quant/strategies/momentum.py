"""简单动量示例策略。"""

from __future__ import annotations

import polars as pl

from northstar_quant.strategies.base import DateBasedStrategyBase


class MomentumRotationStrategy(DateBasedStrategyBase):
    """基础动量轮动示例。

    这个策略主要用于演示：
    - 如何从行情生成信号
    - 如何输出统一目标仓位格式
    """

    strategy_id = "momentum"

    def __init__(self, lookback_days: int = 20, top_n: int = 3) -> None:
        self.lookback_days = lookback_days
        self.top_n = top_n

    def generate_targets(self, market_df: pl.DataFrame) -> pl.DataFrame:
        self.validate_market_data(market_df)
        close_wide = market_df.pivot(index="date", on="symbol", values="close").sort("date")
        pdf = close_wide.to_pandas().set_index("date")
        returns = pdf / pdf.shift(self.lookback_days) - 1.0

        rows = []
        for dt, row in returns.dropna().iterrows():
            ranked = row.sort_values(ascending=False).head(self.top_n)
            weight = 1.0 / max(len(ranked), 1)
            for symbol, value in ranked.items():
                rows.append(
                    {
                        "date": dt.date() if hasattr(dt, "date") else dt,
                        "symbol": symbol,
                        "signal_value": float(value),
                        "target_weight": float(weight),
                    }
                )
        return self.to_targets_frame(rows)
