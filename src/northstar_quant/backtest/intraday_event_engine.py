"""分钟级目标仓位事件回测器。"""

from __future__ import annotations

import pandas as pd
import polars as pl

from northstar_quant.backtest.event_engine import BacktestResult


def run_intraday_event_backtest(
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
    *,
    periods_per_year: int = 252 * 390,
) -> BacktestResult:
    """运行一个面向分钟级数据的事件回测。"""

    close_wide = (
        market_df.pivot(index="timestamp", on="symbol", values="close")
        .sort("timestamp")
        .to_pandas()
        .set_index("timestamp")
    )
    returns = close_wide.pct_change().fillna(0.0)
    target_pdf = targets.to_pandas()

    if "timestamp" in target_pdf.columns:
        target_pdf["timestamp"] = pd.to_datetime(target_pdf["timestamp"])
        weight_pivot = target_pdf.pivot(index="timestamp", columns="symbol", values="target_weight").fillna(0.0)
        weight_pivot = weight_pivot.reindex(returns.index).ffill().fillna(0.0)
    else:
        target_pdf["date"] = pd.to_datetime(target_pdf["date"])
        target_daily = target_pdf.pivot(index="date", columns="symbol", values="target_weight").fillna(0.0)
        session_index = pd.DatetimeIndex(returns.index.normalize())
        weight_pivot = target_daily.reindex(session_index, method="ffill").fillna(0.0)
        weight_pivot.index = returns.index

    portfolio_returns = (weight_pivot.shift(1).fillna(0.0) * returns).sum(axis=1)
    equity = (1.0 + portfolio_returns).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    annualized_return = float(equity.iloc[-1] ** (periods_per_year / max(len(equity), 1)) - 1.0)

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_drawdown = float(drawdown.min())
    turnover = float(weight_pivot.diff().abs().sum(axis=1).mean())

    monthly_returns = portfolio_returns.resample("ME").apply(lambda s: (1.0 + s).prod() - 1.0)
    equity_curve = [
        {"date": idx.strftime("%Y-%m-%d %H:%M:%S"), "equity": float(val)}
        for idx, val in equity.items()
    ]
    drawdown_curve = [
        {"date": idx.strftime("%Y-%m-%d %H:%M:%S"), "drawdown": float(val)}
        for idx, val in drawdown.items()
    ]
    monthly_return_rows = [
        {"month": idx.strftime("%Y-%m"), "return": float(val)}
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
