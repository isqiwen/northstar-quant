"""执行意图事件回测器。"""

from __future__ import annotations

import pandas as pd
import polars as pl

from northstar_quant.backtest.event_engine import BacktestResult
from northstar_quant.common.enums import OrderSemantic
from northstar_quant.execution.intent_semantics import resolve_execution_intent_qty


def run_execution_intent_backtest(
    market_df: pl.DataFrame,
    intents: pl.DataFrame,
    *,
    periods_per_year: int = 252 * 390,
    initial_cash: float = 100000.0,
) -> BacktestResult:
    """根据执行意图驱动简化事件回测。"""

    if market_df.is_empty():
        return BacktestResult(
            total_return=0.0,
            annualized_return=0.0,
            max_drawdown=0.0,
            turnover_estimate=0.0,
            equity_curve=[],
            drawdown_curve=[],
            monthly_returns=[],
        )

    close_wide = (
        market_df.pivot(index="timestamp", on="symbol", values="close")
        .sort("timestamp")
        .to_pandas()
        .set_index("timestamp")
    )
    intent_pdf = intents.to_pandas()
    intent_pdf["timestamp"] = pd.to_datetime(intent_pdf["timestamp"])
    intent_groups = {
        timestamp: group.to_dict("records")
        for timestamp, group in intent_pdf.groupby("timestamp")
    }

    cash = float(initial_cash)
    positions: dict[str, float] = {symbol: 0.0 for symbol in close_wide.columns}
    equity_points: list[tuple[pd.Timestamp, float]] = []
    turnover_points: list[float] = []

    for timestamp, price_row in close_wide.iterrows():
        current_equity = cash + sum(
            float(positions[symbol]) * float(price_row[symbol])
            for symbol in close_wide.columns
            if pd.notna(price_row[symbol])
        )
        traded_notional = 0.0

        for row in intent_groups.get(timestamp, []):
            symbol = str(row["symbol"])
            price = float(price_row.get(symbol, 0.0) or 0.0)
            if price <= 0:
                continue

            size_fraction = float(row.get("size_fraction", 0.0) or 0.0)
            if size_fraction <= 0:
                continue

            side = str(row.get("side", "BUY")).upper()
            order_semantic = str(
                row.get("order_semantic") or OrderSemantic.ENTRY.value
            ).lower()
            desired_qty = resolve_execution_intent_qty(
                side=side,
                order_semantic=order_semantic,
                size_fraction=size_fraction,
                price=price,
                equity=current_equity,
                current_qty=float(positions.get(symbol, 0.0)),
            )
            if desired_qty <= 0:
                continue

            if side == "BUY":
                affordable_qty = int(max(cash, 0.0) // price)
                qty = min(desired_qty, affordable_qty)
            else:
                if order_semantic in (OrderSemantic.EXIT.value, OrderSemantic.REDUCE.value):
                    available_qty = int(max(float(positions.get(symbol, 0.0)), 0.0))
                else:
                    available_qty = desired_qty
                qty = min(desired_qty, available_qty)

            if qty <= 0:
                continue

            signed_qty = float(qty if side == "BUY" else -qty)
            positions[symbol] = float(positions.get(symbol, 0.0)) + signed_qty
            cash -= signed_qty * price
            traded_notional += abs(signed_qty) * price

        equity = cash + sum(
            float(positions[symbol]) * float(price_row[symbol])
            for symbol in close_wide.columns
            if pd.notna(price_row[symbol])
        )
        equity_points.append((timestamp, float(equity)))
        turnover_points.append(traded_notional / max(abs(current_equity), 1.0))

    equity_series = pd.Series(
        [value for _, value in equity_points],
        index=pd.DatetimeIndex([timestamp for timestamp, _ in equity_points]),
    )
    returns = equity_series.pct_change().fillna(0.0)
    ending_ratio = float(equity_series.iloc[-1] / initial_cash) if initial_cash else 0.0
    total_return = float(ending_ratio - 1.0)
    if ending_ratio <= 0:
        annualized_return = -1.0
    else:
        annualized_return = float(
            ending_ratio ** (periods_per_year / max(len(equity_series), 1)) - 1.0
        )
    running_max = equity_series.cummax()
    drawdown = equity_series / running_max - 1.0
    max_drawdown = float(drawdown.min())
    turnover = float(pd.Series(turnover_points, index=equity_series.index).mean())
    monthly_returns = returns.resample("ME").apply(lambda series: (1.0 + series).prod() - 1.0)

    return BacktestResult(
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=max_drawdown,
        turnover_estimate=turnover,
        equity_curve=[
            {"date": idx.strftime("%Y-%m-%d %H:%M:%S"), "equity": float(value)}
            for idx, value in equity_series.items()
        ],
        drawdown_curve=[
            {"date": idx.strftime("%Y-%m-%d %H:%M:%S"), "drawdown": float(value)}
            for idx, value in drawdown.items()
        ],
        monthly_returns=[
            {"month": idx.strftime("%Y-%m"), "return": float(value)}
            for idx, value in monthly_returns.items()
        ],
    )
