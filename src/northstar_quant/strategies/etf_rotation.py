"""真实可落地的 ETF 日频轮动策略。"""

from __future__ import annotations

import polars as pl

from northstar_quant.strategies.base import DailyStrategyBase


class ETFDailyRotationStrategy(DailyStrategyBase):
    """ETF 日频轮动策略。

    策略逻辑：
    1. 使用中期动量（默认 126 日）衡量强弱
    2. 每日选出排名最高的 Top N ETF
    3. 对入选 ETF 等权分配
    4. 输出统一目标仓位

    这是一类非常适合个人量化起步并逐步实盘化的策略：
    - 标的数少
    - 逻辑清晰
    - 易于回测和解释
    """

    strategy_id = "etf_rotation"

    def __init__(self, lookback_days: int = 126, top_n: int = 3) -> None:
        self.lookback_days = lookback_days
        self.top_n = top_n

    def generate_targets(self, market_df: pl.DataFrame) -> pl.DataFrame:
        self.validate_market_data(market_df)
        close_wide = market_df.pivot(index="date", on="symbol", values="close").sort("date")
        pdf = close_wide.to_pandas().set_index("date")
        momentum = pdf / pdf.shift(self.lookback_days) - 1.0

        rows = []
        for dt, row in momentum.dropna().iterrows():
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


US_ETFDailyRotationStrategy = ETFDailyRotationStrategy
