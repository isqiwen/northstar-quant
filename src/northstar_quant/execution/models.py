"""执行层数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class OrderRequest:
    """统一订单请求模型。"""

    strategy_id: str
    symbol: str
    side: str
    qty: float
    target_weight: float | None = None
    order_type: str = "MKT"
    limit_price: float | None = None
    order_semantic: str | None = None
    account: str | None = None
    reason: str = "rebalance"


@dataclass(slots=True)
class OrderResult:
    """统一订单结果模型。"""

    accepted: bool
    broker_order_id: str
    status: str
    message: str = ""
    submitted_at: datetime | None = None


@dataclass(slots=True)
class PositionSnapshot:
    """券商侧持仓快照。"""

    symbol: str
    qty: float
    avg_cost: float | None = None
    market_price: float | None = None
    market_value: float | None = None
    account: str | None = None
    asof: datetime | None = None


@dataclass(slots=True)
class FillSnapshot:
    """券商侧成交快照。"""

    broker_order_id: str
    symbol: str
    qty: float
    price: float
    side: str
    filled_at: datetime | None = None
    account: str | None = None


@dataclass(slots=True)
class RebalanceOrderPlan:
    """再平衡计划结果。

    这里不是券商订单，而是“基于当前持仓与目标权重计算出来的执行意图”。
    先有计划，再过风控，再变成真实订单。
    """

    symbol: str
    side: str
    qty: float
    target_weight: float | None = None
    current_qty: float | None = None
    target_qty: float | None = None
    latest_price: float | None = None
    estimated_trade_value: float | None = None
    strategy_id: str = "core_portfolio"
    order_semantic: str | None = None
    reason: str = "daily_rebalance"
    order_type: str = "MKT"
    limit_price: float | None = None


@dataclass(slots=True)
class BrokerStateSnapshot:
    """券商状态快照，用于对账与健康检查。"""

    positions: list[PositionSnapshot] = field(default_factory=list)
    open_orders: list[dict] = field(default_factory=list)
    fills: list[FillSnapshot] = field(default_factory=list)
    account_values: dict[str, float | str] = field(default_factory=dict)
    asof: datetime | None = None
