"""日频回测成交到共享绩效模块的适配器。"""

from __future__ import annotations

from northstar_quant.backtest.daily_stateful import DailyStatefulBacktestResult
from northstar_quant.performance.models import ExecutionFill, TradeAnalysis
from northstar_quant.performance.trade_analysis import analyze_long_only_fills


def analyze_daily_stateful_result(result: DailyStatefulBacktestResult) -> TradeAnalysis:
    """归并日频状态机成交并生成共享交易统计。

    当前目标权重回测没有初始止损元数据，因此其交易明细会有货币盈亏但 ``pnl_r``
    为 null。带止损的策略应在未来把初始风险写入 ``ExecutionFill``，而不是在此处
    根据价格序列回推。
    """

    fills = tuple(
        ExecutionFill(
            fill_id=fill.order_id,
            timestamp=fill.trade_date,
            symbol=fill.symbol,
            side=fill.side,
            qty=float(fill.qty),
            price=fill.execution_price,
            commission=fill.commission,
        )
        for fill in result.fills
    )
    return analyze_long_only_fills(fills)
