"""分钟订单回放的公开模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import math

from northstar_quant.research.backtest.futures_daily.models import require_actual_instrument_id


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderOffset(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class FuturesIntradayBar:
    """具体合约的一根分钟线、盘口快照和当日动态规则。"""

    trading_day: date
    timestamp: datetime
    instrument_id: str
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    open_interest: float
    bid_price: float
    ask_price: float
    bid_volume: float
    ask_volume: float
    settlement: float
    pre_settlement: float
    upper_limit: float
    lower_limit: float
    margin_rate: float
    commission_open_per_lot: float
    commission_open_rate: float
    commission_close_per_lot: float
    commission_close_rate: float
    commission_close_today_per_lot: float
    commission_close_today_rate: float
    max_position_lots: int
    is_trading_day_end: bool
    session_complete: bool

    def __post_init__(self) -> None:
        require_actual_instrument_id(
            self.instrument_id,
            field_name="分钟线 instrument_id",
        )
        prices = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.bid_price,
            self.ask_price,
            self.settlement,
            self.pre_settlement,
            self.upper_limit,
            self.lower_limit,
        )
        if any(not math.isfinite(value) or value <= 0 for value in prices):
            raise ValueError("分钟回放价格必须是正有限数")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("分钟线 high 必须覆盖 open、low 与 close")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("分钟线 low 必须覆盖 open、high 与 close")
        if (
            self.lower_limit >= self.upper_limit
            or self.low < self.lower_limit
            or self.high > self.upper_limit
            or not self.lower_limit <= self.bid_price <= self.ask_price <= self.upper_limit
        ):
            raise ValueError("分钟 OHLC/盘口必须位于涨跌停范围内")
        if self.session not in {"night", "day"}:
            raise ValueError("分钟 session 仅允许 night 或 day")
        if not self.session_complete:
            raise ValueError("交易日分钟数据未完整覆盖夜盘/日盘")
        if not 0 < self.margin_rate <= 1:
            raise ValueError("分钟 margin_rate 必须位于 (0, 1]")
        non_negative = (
            self.volume,
            self.open_interest,
            self.bid_volume,
            self.ask_volume,
            self.commission_open_per_lot,
            self.commission_open_rate,
            self.commission_close_per_lot,
            self.commission_close_rate,
            self.commission_close_today_per_lot,
            self.commission_close_today_rate,
        )
        if any(not math.isfinite(value) or value < 0 for value in non_negative):
            raise ValueError("分钟成交量、盘口量和手续费必须是非负有限数")
        if isinstance(self.max_position_lots, bool) or self.max_position_lots <= 0:
            raise ValueError("分钟 max_position_lots 必须是正整数")


@dataclass(frozen=True, slots=True)
class ReplayOrderRequest:
    """历史时点提交的委托请求；只能从后续分钟开始参与撮合。"""

    order_id: str
    submitted_at: datetime
    instrument_id: str
    side: OrderSide
    offset: OrderOffset
    qty: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    reason: str = "manual"
    ttl_bars: int = 1
    depends_on_order_id: str | None = None

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("order_id 不能为空")
        require_actual_instrument_id(self.instrument_id, field_name="委托 instrument_id")
        if not isinstance(self.side, OrderSide):
            raise ValueError("委托 side 必须是 OrderSide")
        if not isinstance(self.offset, OrderOffset):
            raise ValueError("委托 offset 必须是 OrderOffset")
        if not isinstance(self.order_type, OrderType):
            raise ValueError("委托 order_type 必须是 OrderType")
        if isinstance(self.qty, bool) or not isinstance(self.qty, int) or self.qty <= 0:
            raise ValueError("委托 qty 必须是正整数")
        if self.ttl_bars < 1:
            raise ValueError("委托 ttl_bars 至少为 1")
        if self.order_type == OrderType.LIMIT:
            if (
                self.limit_price is None
                or not math.isfinite(self.limit_price)
                or self.limit_price <= 0
            ):
                raise ValueError("限价单必须提供正有限 limit_price")
        elif self.limit_price is not None:
            raise ValueError("市价单不得提供 limit_price")
        if self.depends_on_order_id == self.order_id:
            raise ValueError("委托不能依赖自身")


@dataclass(frozen=True, slots=True)
class ReplayCancellation:
    order_id: str
    requested_at: datetime

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("撤单 order_id 不能为空")


@dataclass(frozen=True, slots=True)
class IntradayWeightTarget:
    """已映射到执行交易日和实际合约的保证金权重目标。"""

    decision_day: date
    execution_day: date
    instrument_id: str
    target_weight: float

    def __post_init__(self) -> None:
        require_actual_instrument_id(self.instrument_id, field_name="权重目标 instrument_id")
        if self.execution_day <= self.decision_day:
            raise ValueError("权重目标 execution_day 必须晚于 decision_day")
        if not math.isfinite(self.target_weight) or abs(self.target_weight) > 1:
            raise ValueError("权重目标 target_weight 必须位于 [-1, 1]")


@dataclass(slots=True)
class ReplayOrder:
    """回放状态机中的可变委托状态。"""

    request: ReplayOrderRequest
    status: OrderStatus = OrderStatus.SUBMITTED
    filled_qty: int = 0
    average_fill_price: float | None = None
    commission: float = 0.0
    eligible_bars_seen: int = 0
    queue_ahead_remaining: int | None = None
    message: str = ""
    updated_at: datetime | None = None

    @property
    def remaining_qty(self) -> int:
        return self.request.qty - self.filled_qty


@dataclass(slots=True)
class FuturesIntradayTrade:
    trading_day: date
    timestamp: datetime
    order_id: str
    instrument_id: str
    side: str
    offset: str
    qty: int
    price: float
    commission: float
    reason: str


@dataclass(slots=True)
class FuturesReplayResult:
    final_equity: float
    equity_curve: list[dict[str, object]] = field(default_factory=list)
    trades: list[FuturesIntradayTrade] = field(default_factory=list)
    orders: list[ReplayOrder] = field(default_factory=list)
    rejected_orders: list[str] = field(default_factory=list)
