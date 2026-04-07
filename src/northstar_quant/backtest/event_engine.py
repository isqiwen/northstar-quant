"""轻量事件回测引擎。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import polars as pl


@dataclass(slots=True)
class BacktestResult:
    """回测结果摘要。

    除了最常用的核心指标，本对象还额外保留净值曲线、回撤曲线与月度收益，
    方便报告层生成更像正式研究材料的图表页。
    """

    total_return: float
    annualized_return: float
    max_drawdown: float
    turnover_estimate: float
    equity_curve: list[dict[str, float | str]] = field(default_factory=list)
    drawdown_curve: list[dict[str, float | str]] = field(default_factory=list)
    monthly_returns: list[dict[str, float | str]] = field(default_factory=list)


def run_event_backtest(
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
    *,
    periods_per_year: int = 252,
) -> BacktestResult:
    """运行一个简化但可解释的事件回测。

    当前实现聚焦在“个人量化骨架”场景：
    - 以日频 close-to-close 收益为主
    - 使用目标权重序列构造组合收益
    - 返回最常用的核心指标
    - 额外输出可供报告系统使用的净值 / 回撤 / 月度收益数据
    """

    close_wide = (
        market_df.pivot(index="date", on="symbol", values="close")
        .sort("date")
        .to_pandas()
        .set_index("date")
    )
    returns = close_wide.pct_change().fillna(0.0)

    tgt = targets.to_pandas()
    weight_pivot = tgt.pivot(index="date", columns="symbol", values="target_weight").fillna(0.0)
    weight_pivot = weight_pivot.reindex(returns.index).ffill().fillna(0.0)

    portfolio_returns = (weight_pivot.shift(1).fillna(0.0) * returns).sum(axis=1)
    equity = (1.0 + portfolio_returns).cumprod()

    total_return = float(equity.iloc[-1] - 1.0)
    annualized_return = float(equity.iloc[-1] ** (periods_per_year / max(len(equity), 1)) - 1.0)

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_drawdown = float(drawdown.min())

    turnover = float(weight_pivot.diff().abs().sum(axis=1).mean())

    monthly_returns = (
        portfolio_returns.resample("ME").apply(lambda s: (1.0 + s).prod() - 1.0)
        if isinstance(portfolio_returns.index, pd.DatetimeIndex)
        else pd.Series(dtype=float)
    )

    equity_curve = [
        {'date': idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx), 'equity': float(val)}
        for idx, val in equity.items()
    ]
    drawdown_curve = [
        {'date': idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx), 'drawdown': float(val)}
        for idx, val in drawdown.items()
    ]
    monthly_return_rows = [
        {'month': idx.strftime('%Y-%m') if hasattr(idx, 'strftime') else str(idx), 'return': float(val)}
        for idx, val in monthly_returns.items()
    ]

    return BacktestResult(
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=max_drawdown,
        turnover_estimate=turnover,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
        monthly_returns=monthly_return_rows,
    )
