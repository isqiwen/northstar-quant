"""执行层数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from northstar_quant.trading_execution.orders.state_machine import (
    BrokerOrderStatus,
    canonicalize_broker_order_status,
)


@dataclass(slots=True)
class OrderRequest:
    """准备提交给券商的统一订单载荷。

    它是“订单请求”，尚未表示券商已接受。``instrument_id`` 与 ``exchange_id`` 是实际
    CTP 合约身份；连续研究 symbol 不能仅凭 ``symbol`` 进入真实提交。``run_id``、
    ``batch_id``、``plan_id``、``attempt_no`` 共同用于幂等、恢复和审计。
    """

    strategy_id: str
    symbol: str
    side: str
    qty: float
    profile_id: str | None = None
    target_weight: float | None = None
    order_type: str = "MKT"
    limit_price: float | None = None
    order_semantic: str | None = None
    account: str | None = None
    reason: str = "rebalance"
    reference_price: float | None = None
    reference_price_source: str | None = None
    planned_trade_value: float | None = None
    run_id: str | None = None
    batch_id: str | None = None
    plan_id: str | None = None
    attempt_no: int = 1
    execution_policy_fingerprint: str | None = None
    execution_planner_id: str | None = None
    instrument_id: str | None = None
    exchange_id: str | None = None
    ctp_offset: str | None = None
    volume_multiple: int | None = None
    margin_rate: float | None = None
    required_margin: float | None = None
    order_ref: str | None = None
    currency: str | None = None


@dataclass(slots=True)
class OrderResult:
    """一次提交尝试的可持久化结果。

    ``accepted`` 只表示适配器接受或复用了结果，不表示完全成交；最终成交必须以后续
    状态同步和 FillSnapshot 为准。``replayed`` 为 true 表示命中幂等记录，未再次下单。
    """

    accepted: bool
    broker_order_id: str
    status: str
    message: str = ""
    submitted_at: datetime | None = None
    replayed: bool = False
    client_id: int | None = None
    perm_id: int | None = None

    @property
    def canonical_status(self) -> BrokerOrderStatus:
        """Unknown broker wording is never upgraded to an accepted state."""

        return canonicalize_broker_order_status(self.status)


@dataclass(slots=True)
class PositionSnapshot:
    """某一时点从券商获得的单合约持仓快照。

    ``qty`` 使用带方向的净数量；``sellable_qty`` 是可立即平仓数量，不可用时应保持
    None 而不是假设等于 qty。``asof`` 与 ``snapshot_batch_id`` 用于判断快照完整性。
    """

    symbol: str
    qty: float
    avg_cost: float | None = None
    market_price: float | None = None
    market_value: float | None = None
    sellable_qty: float | None = None
    account: str | None = None
    instrument_id: str | None = None
    exchange_id: str | None = None
    long_today_qty: float | None = None
    long_yesterday_qty: float | None = None
    short_today_qty: float | None = None
    short_yesterday_qty: float | None = None
    long_frozen_qty: float | None = None
    short_frozen_qty: float | None = None
    long_closable_qty: float | None = None
    short_closable_qty: float | None = None
    margin: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    asof: datetime | None = None
    snapshot_batch_id: str | None = None


@dataclass(slots=True)
class FillSnapshot:
    """券商确认的一笔成交，不是订单状态推测。

    ``exec_id`` 应是券商侧唯一成交标识，用于去重；``broker_order_id`` 关联订单，
    ``instrument_id`` 与 ``exchange_id`` 保留实际 CTP 合约身份。
    """

    broker_order_id: str
    symbol: str
    qty: float
    price: float
    side: str
    filled_at: datetime | None = None
    account: str | None = None
    exec_id: str | None = None
    order_ref: str | None = None
    perm_id: int | None = None
    client_id: int | None = None
    instrument_id: str | None = None
    exchange_id: str | None = None
    ctp_offset: str | None = None


@dataclass(slots=True)
class MarketQuoteSnapshot:
    """用于估值和预交易价格检查的券商报价快照。

    ``bid/ask/last/close/market_price`` 的可用性随数据权限变化；调用方必须记录 source、
    时间和数据类型，缺失可信价格时真实订单应失败关闭。
    """

    symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    close: float | None = None
    market_price: float | None = None
    market_data_type: int | None = None
    asof: datetime | None = None
    source: str = "broker_snapshot"


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
    execution_reference_price: float | None = None
    estimated_trade_value: float | None = None
    strategy_id: str = "core_portfolio"
    order_semantic: str | None = None
    reason: str = "daily_rebalance"
    order_type: str = "MKT"
    limit_price: float | None = None
    plan_id: str | None = None
    instrument_id: str | None = None
    exchange_id: str | None = None
    ctp_offset: str | None = None
    volume_multiple: int | None = None
    margin_rate: float | None = None
    required_margin: float | None = None


@dataclass(frozen=True, slots=True)
class FuturesExecutionRule:
    """某个具体期货合约在本轮执行使用的动态规则快照。"""

    margin_rate: float
    max_position_lots: int | None = None


@dataclass(slots=True)
class BrokerStateSnapshot:
    """同一时点的券商账户状态，用于对账、预交易检查与恢复。

    ``state_complete`` 为 false 或 ``state_errors`` 非空时，状态不能作为真实交易的完整
    依据。open_orders 与 completed_orders 共同用于恢复不确定订单，fills 用于补齐成交。
    """

    positions: list[PositionSnapshot] = field(default_factory=list)
    open_orders: list[dict] = field(default_factory=list)
    completed_orders: list[dict] = field(default_factory=list)
    fills: list[FillSnapshot] = field(default_factory=list)
    account_values: dict[str, float | str] = field(default_factory=dict)
    account: str | None = None
    state_complete: bool = True
    state_errors: list[str] = field(default_factory=list)
    asof: datetime | None = None
