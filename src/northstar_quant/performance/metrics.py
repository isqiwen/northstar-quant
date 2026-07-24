"""交易和权益曲线的通用绩效指标。"""

from __future__ import annotations

import math

from northstar_quant.performance.models import EquityPoint, TradeMetrics, TradeRecord


def calculate_trade_metrics(trades: tuple[TradeRecord, ...] | list[TradeRecord]) -> TradeMetrics:
    """计算已完成交易的胜率、货币盈亏和 R 倍数期望值。

    胜率按所有已完成交易的净盈亏计算；R 统计只使用具有入场初始止损的交易，避免
    为缺失风险元数据的历史成交构造虚假的 R 倍数。
    """

    closed = tuple(trades)
    winners = tuple(trade for trade in closed if trade.net_pnl > 0)
    losers = tuple(trade for trade in closed if trade.net_pnl < 0)
    breakeven_count = len(closed) - len(winners) - len(losers)
    gross_profit = sum(trade.net_pnl for trade in winners)
    gross_loss = sum(trade.net_pnl for trade in losers)
    rated = tuple(trade for trade in closed if trade.pnl_r is not None)
    winning_r = tuple(trade.pnl_r for trade in rated if trade.pnl_r is not None and trade.pnl_r > 0)
    losing_r = tuple(trade.pnl_r for trade in rated if trade.pnl_r is not None and trade.pnl_r < 0)
    total_r = sum(trade.pnl_r for trade in rated if trade.pnl_r is not None)

    return TradeMetrics(
        closed_trade_count=len(closed),
        winning_trade_count=len(winners),
        losing_trade_count=len(losers),
        breakeven_trade_count=breakeven_count,
        win_rate=len(winners) / len(closed) if closed else None,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=sum(trade.net_pnl for trade in closed),
        profit_factor=gross_profit / abs(gross_loss) if gross_loss < 0 else None,
        rated_trade_count=len(rated),
        total_r=total_r if rated else None,
        average_win_r=sum(winning_r) / len(winning_r) if winning_r else None,
        average_loss_r=sum(losing_r) / len(losing_r) if losing_r else None,
        expectancy_r=total_r / len(rated) if rated else None,
    )


def calculate_max_drawdown(points: tuple[EquityPoint, ...] | list[EquityPoint]) -> float:
    """计算权益曲线的最大回撤，空序列返回 0。"""

    running_max = -math.inf
    max_drawdown = 0.0
    for point in points:
        if not math.isfinite(point.equity) or point.equity < 0:
            raise ValueError("权益必须是大于等于 0 的有限数值")
        running_max = max(running_max, point.equity)
        if running_max > 0:
            max_drawdown = min(max_drawdown, point.equity / running_max - 1.0)
    return max_drawdown
