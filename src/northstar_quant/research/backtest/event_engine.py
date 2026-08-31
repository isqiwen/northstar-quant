"""轻量事件回测引擎。"""

from __future__ import annotations

import math

import pandas as pd  # type: ignore[import-untyped]  # pandas 未提供本项目锁定版本的类型标记。
import polars as pl

from northstar_quant.research.backtest.models import BacktestEngine, BacktestResult


def _drawdown_from_initial_equity(equity: pd.Series) -> pd.Series:
    """以初始归一化权益 1.0 作为回撤高点的下限。"""

    running_max = equity.cummax().clip(lower=1.0)
    return equity / running_max - 1.0


def run_event_backtest(
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
    *,
    periods_per_year: int = 252,
    initial_cash: float = 100_000.0,
    commission_bps: float = 0.0,
    min_commission: float = 0.0,
    slippage_bps: float = 0.0,
    execution_delay_sessions: int = 1,
    lot_size: int = 1,
    sellable_after_sessions: int = 0,
    terminal_flatten: bool = False,
) -> BacktestResult:
    """运行一个带显式成本与延迟假设的收益型事件回测。

    对每个标的计算 ``r_i[t] = close_i[t] / close_i[t-1] - 1``，组合当日收益为
    ``R[t] = Σ(w_i[t-delay] × r_i[t])``。默认延迟一个交易时段，因此不会使用当天
    收盘后才生成的信号获取当天收益。

    每次有效权重变化按变动名义金额扣除单边佣金、最低佣金与滑点。净值曲线仍输出
    以 1 为起点的归一化权益，``initial_cash`` 用于把最低佣金换算成真实权益影响。
    该引擎不模拟保证金、换月、涨跌停和同日 OHLC 成交顺序，只能用于连续合约逻辑
    研究。手数取整与 T+N 可卖状态需要逐笔持仓引擎；传入非默认值时显式拒绝，避免
    配置被静默忽略。

    设置 ``terminal_flatten=True`` 时，会在最后一个阶段内收益结算后，按最后有效权重
    估计一次强制平仓的佣金（含最低佣金）与滑点，并计入最后一个 session 的净值和换手。
    这只是在连续研究序列上消除阶段边界持仓残留的收益近似，不产生订单或成交，也不能
    代替实际合约平仓回放。
    """

    _validate_backtest_assumptions(
        market_df=market_df,
        targets=targets,
        periods_per_year=periods_per_year,
        initial_cash=initial_cash,
        commission_bps=commission_bps,
        min_commission=min_commission,
        slippage_bps=slippage_bps,
        execution_delay_sessions=execution_delay_sessions,
        lot_size=lot_size,
        sellable_after_sessions=sellable_after_sessions,
        terminal_flatten=terminal_flatten,
    )

    close_wide = (
        market_df.pivot(index="date", on="symbol", values="close")
        .sort("date")
        .to_pandas()
        .set_index("date")
    )
    returns = close_wide.pct_change().fillna(0.0)

    tgt = targets.to_pandas()
    weight_pivot = tgt.pivot(index="date", columns="symbol", values="target_weight").fillna(0.0)
    unknown_symbols = sorted(set(weight_pivot.columns).difference(returns.columns))
    if unknown_symbols:
        raise ValueError(
            "目标权重包含行情中不存在的标的：" + ", ".join(str(item) for item in unknown_symbols)
        )
    weight_pivot = (
        weight_pivot.reindex(index=returns.index, columns=returns.columns)
        .ffill()
        .fillna(0.0)
    )

    effective_weights = weight_pivot.shift(execution_delay_sessions).fillna(0.0)
    gross_returns = (effective_weights * returns).sum(axis=1)
    weight_changes = effective_weights.diff().fillna(effective_weights).abs()

    equity_amount = float(initial_cash)
    normalized_equity_values: list[float] = []
    net_return_values: list[float] = []
    turnover_values: list[float] = []
    commission_rate = float(commission_bps) / 10_000.0
    slippage_rate = float(slippage_bps) / 10_000.0

    for row_index in range(len(returns.index)):
        starting_equity = equity_amount
        gross_return = float(gross_returns.iloc[row_index])
        changes = weight_changes.iloc[row_index]
        traded_notionals = changes * starting_equity
        traded_mask = changes > 1e-12
        commission = float(
            sum(
                max(float(notional) * commission_rate, float(min_commission))
                for notional in traded_notionals[traded_mask]
            )
        )
        slippage = float(traded_notionals.sum()) * slippage_rate
        equity_amount = starting_equity * (1.0 + gross_return) - commission - slippage
        if not math.isfinite(equity_amount) or equity_amount <= 0:
            raise ValueError(
                "回测权益已降至零或出现非有限值；请检查目标杠杆、行情与交易成本配置"
            )
        normalized_equity_values.append(equity_amount / float(initial_cash))
        net_return_values.append(equity_amount / starting_equity - 1.0)
        turnover_values.append(float(changes.sum()))

    if terminal_flatten:
        closing_weights = effective_weights.iloc[-1].abs()
        closing_notionals = closing_weights * equity_amount
        closing_mask = closing_weights > 1e-12
        closing_commission = float(
            sum(
                max(float(notional) * commission_rate, float(min_commission))
                for notional in closing_notionals[closing_mask]
            )
        )
        closing_slippage = float(closing_notionals.sum()) * slippage_rate
        equity_amount -= closing_commission + closing_slippage
        if not math.isfinite(equity_amount) or equity_amount <= 0:
            raise ValueError(
                "阶段末强制平仓后回测权益已降至零或出现非有限值；"
                "请检查目标杠杆、行情与交易成本配置"
            )
        normalized_equity_values[-1] = equity_amount / float(initial_cash)
        net_return_values[-1] = equity_amount / starting_equity - 1.0
        turnover_values[-1] += float(closing_weights.sum())

    equity = pd.Series(normalized_equity_values, index=returns.index, dtype=float)
    portfolio_returns = pd.Series(net_return_values, index=returns.index, dtype=float)

    total_return = float(equity.iloc[-1] - 1.0)
    annualized_return = float(
        equity.iloc[-1] ** (periods_per_year / max(len(equity), 1)) - 1.0
    )

    drawdown = _drawdown_from_initial_equity(equity)
    max_drawdown = float(drawdown.min())

    turnover = float(pd.Series(turnover_values, index=returns.index).mean())

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
    turnover_curve = [
        {
            "date": idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx),
            "turnover": float(val),
        }
        for idx, val in pd.Series(turnover_values, index=returns.index).items()
    ]

    return BacktestResult(
        engine=BacktestEngine.WEIGHT_RETURN,
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=max_drawdown,
        turnover_estimate=turnover,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
        monthly_returns=monthly_return_rows,
        turnover_curve=turnover_curve,
    )


def _validate_backtest_assumptions(
    *,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
    periods_per_year: int,
    initial_cash: float,
    commission_bps: float,
    min_commission: float,
    slippage_bps: float,
    execution_delay_sessions: int,
    lot_size: int,
    sellable_after_sessions: int,
    terminal_flatten: bool,
) -> None:
    """在计算前验证收益型引擎能够诚实执行的假设。"""

    if market_df.is_empty():
        raise ValueError("回测行情不能为空")
    if targets.is_empty():
        raise ValueError("回测目标权重不能为空")
    required_market_columns = {"date", "symbol", "close"}
    required_target_columns = {"date", "symbol", "target_weight"}
    missing_market = sorted(required_market_columns.difference(market_df.columns))
    missing_targets = sorted(required_target_columns.difference(targets.columns))
    if missing_market:
        raise ValueError("回测行情缺少字段：" + ", ".join(missing_market))
    if missing_targets:
        raise ValueError("回测目标缺少字段：" + ", ".join(missing_targets))
    if periods_per_year <= 0:
        raise ValueError("periods_per_year 必须大于 0")
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash 必须是大于 0 的有限数")
    for field_name, value in (
        ("commission_bps", commission_bps),
        ("min_commission", min_commission),
        ("slippage_bps", slippage_bps),
    ):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{field_name} 必须是非负有限数")
    if execution_delay_sessions < 1:
        raise ValueError("execution_delay_sessions 至少为 1")
    if lot_size != 1:
        raise ValueError(
            "weight_return 引擎不支持手数取整；lot_size 必须为 1，"
            "实际手数请使用逐笔持仓回测器"
        )
    if sellable_after_sessions != 0:
        raise ValueError(
            "weight_return 引擎不支持 T+N 可卖状态；sellable_after_sessions 必须为 0"
        )
    if not isinstance(terminal_flatten, bool):
        raise ValueError("terminal_flatten 必须是 bool")
