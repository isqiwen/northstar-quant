"""交易绩效归因的纯领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


Timestamp = date | datetime


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    """统一成交输入，供回测、paper 与实盘归因共用。

    ``initial_stop_price`` 与 ``target_r`` 是入场时确定的风险元数据。它们为
    ``None`` 时，仍能计算货币盈亏，但不能可靠计算盈亏 R；调用方不得在成交后
    根据历史价格猜测初始止损。
    """

    fill_id: str
    timestamp: Timestamp
    symbol: str
    side: str
    qty: float
    price: float
    commission: float = 0.0
    strategy_id: str | None = None
    initial_stop_price: float | None = None
    target_r: float | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """一段 FIFO 入场数量被平仓后形成的已完成交易切片。

    部分平仓可能把一次入场拆成多条记录；每条记录都保留对应数量的入场、出场、
    费用和初始风险，从而可准确聚合为胜率、期望值和 R 倍数。
    """

    trade_id: str
    symbol: str
    strategy_id: str | None
    entry_fill_id: str
    exit_fill_id: str
    entry_time: Timestamp
    exit_time: Timestamp
    qty: float
    entry_price: float
    exit_price: float
    initial_stop_price: float | None
    target_r: float | None
    entry_commission: float
    exit_commission: float
    gross_pnl: float
    net_pnl: float
    initial_risk: float | None
    pnl_r: float | None
    entry_reason: str | None
    exit_reason: str | None


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    """已完成交易的统计摘要。"""

    closed_trade_count: int
    winning_trade_count: int
    losing_trade_count: int
    breakeven_trade_count: int
    win_rate: float | None
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float | None
    rated_trade_count: int
    total_r: float | None
    average_win_r: float | None
    average_loss_r: float | None
    expectancy_r: float | None


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """账户或回测权益时间点，用于统一计算回撤。"""

    timestamp: Timestamp
    equity: float


@dataclass(frozen=True, slots=True)
class TradeAnalysis:
    """归并后的交易明细、统计和仍未平仓数量。"""

    trades: tuple[TradeRecord, ...]
    metrics: TradeMetrics
    open_qty_by_symbol: dict[str, float]
