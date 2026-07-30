"""实际合约逐日回测的公开模型与基础校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math


@dataclass(frozen=True, slots=True)
class FuturesInstrumentSpec:
    """实际合约的静态交易规格。"""

    instrument_id: str
    product: str
    exchange_id: str
    multiplier: float
    tick_size: float
    slippage_ticks: float = 0.0

    def __post_init__(self) -> None:
        require_actual_instrument_id(self.instrument_id, field_name="instrument_id")
        if not self.product.strip() or self.product.strip().upper().endswith("_CONT"):
            raise ValueError("期货合约规格必须包含实际品种 product")
        if not self.exchange_id.strip():
            raise ValueError("期货合约规格必须包含 exchange_id")
        if self.multiplier <= 0 or self.tick_size <= 0:
            raise ValueError("期货合约 multiplier 和 tick_size 必须大于 0")
        if self.slippage_ticks < 0:
            raise ValueError("期货合约 slippage_ticks 不能为负数")


@dataclass(frozen=True, slots=True)
class FuturesDailyBar:
    """具体合约的完整交易日日线与动态规则快照。"""

    trading_day: date
    instrument_id: str
    open: float
    high: float
    low: float
    close: float
    settlement: float
    pre_settlement: float
    volume: float
    open_interest: float
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
    first_session: str
    session_complete: bool

    def __post_init__(self) -> None:
        require_actual_instrument_id(self.instrument_id, field_name="日线 instrument_id")
        prices = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.settlement,
            self.pre_settlement,
            self.upper_limit,
            self.lower_limit,
        )
        if any(not math.isfinite(value) or value <= 0 for value in prices):
            raise ValueError("期货日线价格必须是正有限数")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("日线 high 必须覆盖 open、low 与 close")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("日线 low 必须覆盖 open、high 与 close")
        if (
            self.lower_limit >= self.upper_limit
            or self.low < self.lower_limit
            or self.high > self.upper_limit
        ):
            raise ValueError("期货日线 OHLC 必须位于涨跌停价格范围内")
        if not 0 < self.margin_rate <= 1:
            raise ValueError("期货日线 margin_rate 必须位于 (0, 1]")
        non_negative = (
            self.volume,
            self.open_interest,
            self.commission_open_per_lot,
            self.commission_open_rate,
            self.commission_close_per_lot,
            self.commission_close_rate,
            self.commission_close_today_per_lot,
            self.commission_close_today_rate,
        )
        if any(not math.isfinite(value) or value < 0 for value in non_negative):
            raise ValueError("成交量、持仓量和手续费字段必须是非负有限数")
        if isinstance(self.max_position_lots, bool) or self.max_position_lots <= 0:
            raise ValueError("max_position_lots 必须是正整数")
        if self.first_session not in {"night", "day"}:
            raise ValueError("first_session 仅允许 night 或 day")
        if not self.session_complete:
            raise ValueError("交易日未完整覆盖夜盘/日盘，已拒绝回测")


@dataclass(frozen=True, slots=True)
class FuturesTarget:
    """决策日产生的目标手数；正数多头、负数空头。"""

    decision_day: date
    instrument_id: str
    target_qty: int
    stop_price: float | None = None
    target_price: float | None = None

    def __post_init__(self) -> None:
        require_actual_instrument_id(self.instrument_id, field_name="目标 instrument_id")
        if isinstance(self.target_qty, bool) or not isinstance(self.target_qty, int):
            raise ValueError("期货目标手数必须是整数")
        _validate_optional_prices(self.stop_price, self.target_price, self.target_qty)


@dataclass(frozen=True, slots=True)
class FuturesWeightTarget:
    """按保证金占用比例表达的目标，用执行日权益换算为整数手数。"""

    decision_day: date
    instrument_id: str
    target_weight: float

    def __post_init__(self) -> None:
        require_actual_instrument_id(self.instrument_id, field_name="权重目标 instrument_id")
        if not math.isfinite(self.target_weight) or abs(self.target_weight) > 1:
            raise ValueError("期货 target_weight 必须是 [-1, 1] 内的有限数")


@dataclass(frozen=True, slots=True)
class FuturesRollover:
    """生效交易日开盘执行的显式换月。"""

    trading_day: date
    from_instrument_id: str
    to_instrument_id: str

    def __post_init__(self) -> None:
        require_actual_instrument_id(self.from_instrument_id, field_name="换月旧 instrument_id")
        require_actual_instrument_id(self.to_instrument_id, field_name="换月新 instrument_id")
        if self.from_instrument_id.upper() == self.to_instrument_id.upper():
            raise ValueError("换月的旧合约与新合约不能相同")


@dataclass(slots=True)
class FuturesTrade:
    """一次模拟成交；价格包含方向性滑点。"""

    trading_day: date
    instrument_id: str
    side: str
    qty: int
    price: float
    commission: float
    reason: str


@dataclass(slots=True)
class FuturesDailyBacktestResult:
    """逐日回测结果；现金已按结算价盯市，因此等于日终权益。"""

    final_equity: float
    equity_curve: list[dict[str, object]] = field(default_factory=list)
    trades: list[FuturesTrade] = field(default_factory=list)
    rejected_targets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PositionState:
    """状态机内部净持仓及当日开仓数量。"""

    qty: int
    settlement_price: float
    stop_price: float | None
    target_price: float | None
    opened_today_qty: int = 0


def require_actual_instrument_id(value: str, *, field_name: str) -> None:
    """拒绝连续研究代码作为可成交合约。"""

    normalized = str(value).strip().upper()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if normalized.endswith("_CONT"):
        raise ValueError(f"{field_name} 不得使用连续研究合约：{normalized}")


def _validate_optional_prices(
    stop_price: float | None,
    target_price: float | None,
    target_qty: int,
) -> None:
    for field_name, value in (
        ("stop_price", stop_price),
        ("target_price", target_price),
    ):
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(f"期货目标 {field_name} 必须是正有限数")
    if stop_price is not None and target_price is not None:
        if target_qty > 0 and stop_price >= target_price:
            raise ValueError("多头初始止损价必须低于止盈价")
        if target_qty < 0 and stop_price <= target_price:
            raise ValueError("空头初始止损价必须高于止盈价")
