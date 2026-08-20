"""国内期货连续合约的中低频趋势研究策略。"""

from __future__ import annotations

import polars as pl

from northstar_quant.research.strategies.base import DailyStrategyBase


class FuturesTrendStrategy(DailyStrategyBase):
    """以中期收益方向生成连续合约的多空目标权重。

    信号公式为 ``close[t] / close[t-lookback] - 1``。正信号持多，负信号持空，
    每个有效连续合约获得相同的绝对权重。它只服务于连续合约研究；实际下单必须
    映射到当期可交易合约，并另行处理保证金、换月、涨跌停和交易时段。
    """

    strategy_id = "futures_trend"
    required_market_columns = ("symbol", "close")

    def __init__(self, lookback_days: int = 60) -> None:
        """创建趋势策略。

        ``lookback_days`` 是“当前收盘价与多少期前收盘价”的间隔，而不是自然日数；
        缺少交易日、停盘或上市不足窗口的品种不会产生该日信号。最小值为 2，避免把
        过短窗口误当成中低频趋势。
        """

        if lookback_days < 2:
            raise ValueError("lookback_days 必须大于等于 2")
        self.lookback_days = lookback_days

    def generate_targets(self, market_df: pl.DataFrame) -> pl.DataFrame:
        """按交易日生成等绝对权重的趋势目标仓位。

        对每个 symbol 先按日期透视为收盘价序列，再计算
        ``r[t] = close[t] / close[t-lookback_days] - 1``：

        - ``r[t] > 0``：分配 ``+1/N`` 的多头目标权重；
        - ``r[t] < 0``：分配 ``-1/N`` 的空头目标权重；
        - ``r[t] = 0`` 或价格不完整：不持有该品种。

        ``N`` 是当日有非零有效信号的品种数量，所以输出的绝对权重和为 1。后续策略
        管线会按画像风险上限缩放它。这里仅生成研究目标，不处理换月、保证金、持仓限额
        或实际 CTP 合约选择。
        """

        self.validate_market_data(market_df)
        close_wide = market_df.pivot(index="date", on="symbol", values="close").sort("date")
        returns = close_wide.to_pandas().set_index("date").pct_change(self.lookback_days)

        rows: list[dict[str, object]] = []
        for current_date, signals in returns.dropna(how="all").iterrows():
            valid = signals.dropna()
            active = valid[valid.abs() > 1e-12]
            active_weight = 1.0 / len(active) if not active.empty else 0.0
            for symbol, signal in valid.items():
                # 每个已经形成信号的标的都必须显式给出该期目标。否则下游的
                # 前向填充会把“信号归零”误解成“继续持有上一期仓位”。
                target_weight = (
                    active_weight if signal > 1e-12 else -active_weight if signal < -1e-12 else 0.0
                )
                rows.append(
                    {
                        "date": current_date.date() if hasattr(current_date, "date") else current_date,
                        "symbol": symbol,
                        "signal_value": float(signal),
                        "target_weight": float(target_weight),
                    }
                )
        return self.to_targets_frame(rows)
