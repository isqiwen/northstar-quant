"""国内期货逐日持仓与撮合状态机。

本模块只接受具体可交易合约的日线、合约规格和显式换月指令，绝不接受 ``RB_CONT``
这类连续研究序列。信号在决策日生成，按后续交易日开盘执行；止损/止盈使用日线 OHLC
保守规则：跳空越过止损按开盘价，止损与止盈同日均触及时优先止损。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from northstar_quant.config.product_cards import load_product_cards

@dataclass(frozen=True, slots=True)
class FuturesInstrumentSpec:
    """一份经核验的具体期货合约规格与回测成本假设。"""

    instrument_id: str
    product: str
    exchange_id: str
    multiplier: float
    tick_size: float
    initial_margin_rate: float
    commission_per_lot: float = 0.0
    slippage_ticks: float = 0.0

    def __post_init__(self) -> None:
        if not self.instrument_id.strip() or not self.exchange_id.strip():
            raise ValueError("期货合约规格必须包含 instrument_id 和 exchange_id")
        _require_actual_instrument_id(self.instrument_id, field_name="instrument_id")
        if not self.product.strip() or self.product.strip().upper().endswith("_CONT"):
            raise ValueError("期货合约规格必须包含实际品种 product")
        for name in ("multiplier", "tick_size", "initial_margin_rate"):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"期货合约规格 {name} 必须大于 0")
        if self.initial_margin_rate > 1:
            raise ValueError("initial_margin_rate 必须小于等于 1")
        if self.commission_per_lot < 0 or self.slippage_ticks < 0:
            raise ValueError("手续费和滑点 tick 数不得为负")


@dataclass(frozen=True, slots=True)
class FuturesDailyBar:
    """具体合约的一根日线；价格均为同一报价单位。"""

    trading_day: date
    instrument_id: str
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        _require_actual_instrument_id(self.instrument_id, field_name="日线 instrument_id")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.high < self.low:
            raise ValueError("日线 OHLC 无效")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("日线 high/low 必须覆盖 open 与 close")


@dataclass(frozen=True, slots=True)
class FuturesTarget:
    """决策日产生的目标手数及可选初始止损/止盈；正数多头、负数空头。"""

    decision_day: date
    instrument_id: str
    target_qty: int
    stop_price: float | None = None
    target_price: float | None = None

    def __post_init__(self) -> None:
        _require_actual_instrument_id(self.instrument_id, field_name="目标 instrument_id")


@dataclass(frozen=True, slots=True)
class FuturesRollover:
    """一个交易日开盘执行的显式换月：先平旧合约，再开新合约的同等手数。"""

    trading_day: date
    from_instrument_id: str
    to_instrument_id: str

    def __post_init__(self) -> None:
        _require_actual_instrument_id(self.from_instrument_id, field_name="换月旧 instrument_id")
        _require_actual_instrument_id(self.to_instrument_id, field_name="换月新 instrument_id")
        if self.from_instrument_id == self.to_instrument_id:
            raise ValueError("换月的旧合约与新合约不能相同")


@dataclass(slots=True)
class FuturesTrade:
    """状态机记录的一次开、平或换月成交，价格已包含方向性滑点。"""

    trading_day: date
    instrument_id: str
    side: str
    qty: int
    price: float
    commission: float
    reason: str


@dataclass(slots=True)
class FuturesDailyBacktestResult:
    """逐日回测结果；现金已按每日结算价盯市，因此等于日终权益。"""

    final_equity: float
    equity_curve: list[dict[str, object]] = field(default_factory=list)
    trades: list[FuturesTrade] = field(default_factory=list)
    rejected_targets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Position:
    qty: int
    settlement_price: float
    stop_price: float | None
    target_price: float | None


def run_daily_futures_backtest(
    *,
    bars: Iterable[FuturesDailyBar],
    instrument_specs: Iterable[FuturesInstrumentSpec],
    targets: Iterable[FuturesTarget],
    rollovers: Iterable[FuturesRollover] = (),
    initial_cash: float = 1_000_000.0,
    trading_calendar: Iterable[date] | None = None,
    execution_delay_sessions: int = 1,
) -> FuturesDailyBacktestResult:
    """运行可验证的国内期货逐日状态机。

    处理顺序固定为：显式换月 → 前序信号的开盘调仓 → 日内保护价 → 收盘盯市与
    保证金检查。交易日历为空时从 bars 推导；传入时每根 bar 必须属于该日历。目标
    因保证金不足被拒绝，日终保证金不足则按收盘价强平。该函数不下载数据，也不使用
    连续合约；调用者必须提供真实合约链和已核验的成本/保证金参数。
    """

    if initial_cash <= 0 or execution_delay_sessions < 1:
        raise ValueError("initial_cash 必须大于 0，execution_delay_sessions 必须大于等于 1")
    spec_items = tuple(instrument_specs)
    specs = {item.instrument_id.upper(): item for item in spec_items}
    if not specs:
        raise ValueError("至少需要一个具体期货合约规格")
    if len(specs) != len(spec_items):
        raise ValueError("具体期货合约规格 instrument_id 不能重复")
    _validate_instrument_specs_against_product_cards(specs)
    by_day: dict[date, dict[str, FuturesDailyBar]] = {}
    for bar in bars:
        instrument_id = bar.instrument_id.upper()
        if instrument_id not in specs:
            raise ValueError(f"日线缺少合约规格：{instrument_id}")
        if instrument_id in by_day.setdefault(bar.trading_day, {}):
            raise ValueError(f"日线重复：{bar.trading_day}/{instrument_id}")
        by_day[bar.trading_day][instrument_id] = bar
    if not by_day:
        raise ValueError("至少需要一根具体合约日线")
    sessions = sorted(trading_calendar or by_day)
    if sessions != sorted(set(sessions)) or not sessions:
        raise ValueError("交易日历必须为非空、无重复且升序的交易日列表")
    if any(day not in sessions for day in by_day):
        raise ValueError("日线包含不在交易日历中的日期")

    target_schedule = _schedule_targets(targets, sessions, execution_delay_sessions)
    rollover_schedule = _schedule_rollovers(rollovers, sessions)
    cash = float(initial_cash)
    positions: dict[str, _Position] = {}
    result = FuturesDailyBacktestResult(final_equity=cash)
    for day in sessions:
        day_bars = by_day.get(day, {})
        cash = _process_rollovers(day, day_bars, specs, positions, cash, rollover_schedule, result.trades)
        cash = _process_targets(
            day,
            day_bars,
            specs,
            positions,
            cash,
            target_schedule.get(day, ()),
            result.trades,
            result.rejected_targets,
        )
        cash = _process_protective_exits(day, day_bars, specs, positions, cash, result.trades)
        cash = _settle_and_apply_margin(day, day_bars, specs, positions, cash, result.trades)
        margin = _margin_required(day_bars, specs, positions, use_close=True)
        result.equity_curve.append({"date": day.isoformat(), "equity": cash, "margin": margin})
    result.final_equity = cash
    return result


def _schedule_targets(targets: Iterable[FuturesTarget], sessions: list[date], delay: int) -> dict[date, list[FuturesTarget]]:
    index = {day: offset for offset, day in enumerate(sessions)}
    scheduled: dict[date, list[FuturesTarget]] = {}
    for target in targets:
        if target.decision_day not in index:
            raise ValueError(f"信号决策日不在交易日历中：{target.decision_day}")
        execute_offset = index[target.decision_day] + delay
        if execute_offset < len(sessions):
            scheduled.setdefault(sessions[execute_offset], []).append(target)
    return scheduled


def _schedule_rollovers(rollovers: Iterable[FuturesRollover], sessions: list[date]) -> dict[date, list[FuturesRollover]]:
    scheduled: dict[date, list[FuturesRollover]] = {}
    for rollover in rollovers:
        if rollover.trading_day not in sessions:
            raise ValueError(f"换月日不在交易日历中：{rollover.trading_day}")
        scheduled.setdefault(rollover.trading_day, []).append(rollover)
    return scheduled


def _fill_price(price: float, *, side: str, spec: FuturesInstrumentSpec) -> float:
    return price + spec.tick_size * spec.slippage_ticks * (1 if side == "BUY" else -1)


def _open(day: date, instrument_id: str, signed_qty: int, price: float, reason: str, specs, positions, cash: float, trades) -> float:
    if not signed_qty:
        return cash
    spec = specs[instrument_id]
    side = "BUY" if signed_qty > 0 else "SELL"
    fill = _fill_price(price, side=side, spec=spec)
    commission = abs(signed_qty) * spec.commission_per_lot
    current = positions.get(instrument_id)
    if current is None:
        positions[instrument_id] = _Position(signed_qty, fill, None, None)
    elif current.qty * signed_qty > 0:
        total = abs(current.qty) + abs(signed_qty)
        current.settlement_price = (abs(current.qty) * current.settlement_price + abs(signed_qty) * fill) / total
        current.qty += signed_qty
    else:
        raise ValueError("状态机内部错误：开仓方向与既有持仓冲突")
    trades.append(FuturesTrade(day, instrument_id, side, abs(signed_qty), fill, commission, reason))
    return cash - commission


def _close(day: date, instrument_id: str, qty: int, price: float, reason: str, specs, positions, cash: float, trades) -> float:
    position = positions[instrument_id]
    if qty <= 0 or qty > abs(position.qty):
        raise ValueError("平仓手数超出持仓")
    spec = specs[instrument_id]
    side = "SELL" if position.qty > 0 else "BUY"
    fill = _fill_price(price, side=side, spec=spec)
    cash += (1 if position.qty > 0 else -1) * qty * (fill - position.settlement_price) * spec.multiplier
    commission = qty * spec.commission_per_lot
    cash -= commission
    position.qty += -qty if position.qty > 0 else qty
    trades.append(FuturesTrade(day, instrument_id, side, qty, fill, commission, reason))
    if position.qty == 0:
        del positions[instrument_id]
    return cash


def _process_rollovers(day, bars, specs, positions, cash, schedule, trades) -> float:
    for roll in schedule.get(day, ()):
        old_id, new_id = roll.from_instrument_id.upper(), roll.to_instrument_id.upper()
        if old_id not in specs or new_id not in specs or old_id not in bars or new_id not in bars:
            raise ValueError(f"换月缺少合约规格或开盘日线：{old_id} → {new_id}")
        position = positions.get(old_id)
        if position is None:
            continue
        qty, stop, target = position.qty, position.stop_price, position.target_price
        cash = _close(day, old_id, abs(qty), bars[old_id].open, "roll_close", specs, positions, cash, trades)
        cash = _open(day, new_id, qty, bars[new_id].open, "roll_open", specs, positions, cash, trades)
        positions[new_id].stop_price, positions[new_id].target_price = stop, target
    return cash


def _process_targets(day, bars, specs, positions, cash, targets, trades, rejected) -> float:
    for target in targets:
        instrument_id = target.instrument_id.upper()
        if instrument_id not in specs or instrument_id not in bars:
            raise ValueError(f"目标执行日缺少具体合约开盘日线：{instrument_id}")
        current = positions.get(instrument_id)
        current_qty = current.qty if current else 0
        delta = target.target_qty - current_qty
        if delta and not _target_margin_is_affordable(instrument_id, target.target_qty, bars, specs, positions, cash):
            rejected.append(f"{day}/{instrument_id}: 保证金不足，目标手数 {target.target_qty} 被拒绝")
            continue
        if delta and current_qty and (current_qty > 0) != (target.target_qty > 0):
            cash = _close(day, instrument_id, abs(current_qty), bars[instrument_id].open, "target_close", specs, positions, cash, trades)
            delta = target.target_qty
        if delta < 0 and instrument_id in positions and positions[instrument_id].qty > 0:
            cash = _close(day, instrument_id, min(-delta, positions[instrument_id].qty), bars[instrument_id].open, "target_reduce", specs, positions, cash, trades)
        elif delta > 0 and instrument_id in positions and positions[instrument_id].qty < 0:
            cash = _close(day, instrument_id, min(delta, -positions[instrument_id].qty), bars[instrument_id].open, "target_reduce", specs, positions, cash, trades)
        remaining = target.target_qty - (positions[instrument_id].qty if instrument_id in positions else 0)
        if remaining:
            cash = _open(day, instrument_id, remaining, bars[instrument_id].open, "target_open", specs, positions, cash, trades)
        if instrument_id in positions:
            positions[instrument_id].stop_price = target.stop_price
            positions[instrument_id].target_price = target.target_price
    return cash


def _target_margin_is_affordable(instrument_id, target_qty, bars, specs, positions, cash) -> bool:
    projected = {key: _Position(value.qty, value.settlement_price, value.stop_price, value.target_price) for key, value in positions.items()}
    if target_qty:
        projected[instrument_id] = _Position(target_qty, bars[instrument_id].open, None, None)
    else:
        projected.pop(instrument_id, None)
    return _margin_required(bars, specs, projected, use_close=False) <= cash + 1e-8


def _process_protective_exits(day, bars, specs, positions, cash, trades) -> float:
    for instrument_id in list(positions):
        if instrument_id not in bars:
            raise ValueError(f"持仓合约缺少日线，无法安全盯市：{instrument_id}")
        position, bar = positions[instrument_id], bars[instrument_id]
        exit_price, reason = _protective_exit_price(position, bar)
        if exit_price is not None:
            cash = _close(day, instrument_id, abs(position.qty), exit_price, reason, specs, positions, cash, trades)
    return cash


def _protective_exit_price(position: _Position, bar: FuturesDailyBar) -> tuple[float | None, str]:
    stop, target = position.stop_price, position.target_price
    if position.qty > 0:
        stop_hit = stop is not None and (bar.open <= stop or bar.low <= stop)
        target_hit = target is not None and (bar.open >= target or bar.high >= target)
        if stop_hit:  # 两者同日触发时保守地假定先止损。
            return (bar.open if bar.open <= stop else stop), "stop_loss"
        if target_hit:
            return (bar.open if bar.open >= target else target), "take_profit"
    else:
        stop_hit = stop is not None and (bar.open >= stop or bar.high >= stop)
        target_hit = target is not None and (bar.open <= target or bar.low <= target)
        if stop_hit:
            return (bar.open if bar.open >= stop else stop), "stop_loss"
        if target_hit:
            return (bar.open if bar.open <= target else target), "take_profit"
    return None, ""


def _settle_and_apply_margin(day, bars, specs, positions, cash, trades) -> float:
    for instrument_id, position in list(positions.items()):
        if instrument_id not in bars:
            raise ValueError(f"持仓合约缺少收盘日线：{instrument_id}")
        bar, spec = bars[instrument_id], specs[instrument_id]
        cash += position.qty * (bar.close - position.settlement_price) * spec.multiplier
        position.settlement_price = bar.close
    if _margin_required(bars, specs, positions, use_close=True) > cash + 1e-8:
        for instrument_id in list(positions):
            cash = _close(day, instrument_id, abs(positions[instrument_id].qty), bars[instrument_id].close, "margin_call", specs, positions, cash, trades)
    return cash


def _margin_required(bars, specs, positions, *, use_close: bool) -> float:
    price_field = "close" if use_close else "open"
    return sum(abs(position.qty) * getattr(bars[instrument_id], price_field) * specs[instrument_id].multiplier * specs[instrument_id].initial_margin_rate for instrument_id, position in positions.items())


def _require_actual_instrument_id(value: str, *, field_name: str) -> None:
    """拒绝连续研究符号，强制调用方提供明确可交易的交割月份合约。"""

    normalized = str(value).strip().upper()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if normalized.endswith("_CONT"):
        raise ValueError(f"{field_name} 不得使用连续研究合约：{normalized}")


def _validate_instrument_specs_against_product_cards(specs: dict[str, FuturesInstrumentSpec]) -> None:
    """回测开始前强制加载品种卡，拒绝未知品种或静态规格不一致的实际合约。"""

    cards = {card.product: card for card in load_product_cards()}
    for instrument_id, spec in specs.items():
        product = spec.product.strip().upper()
        card = cards.get(product)
        if card is None:
            raise ValueError(f"实际合约 {instrument_id} 的品种 {product} 缺少品种卡")
        if (spec.exchange_id.strip().upper(), spec.multiplier, spec.tick_size) != (
            card.exchange,
            card.multiplier,
            card.tick_size,
        ):
            raise ValueError(f"实际合约 {instrument_id} 与品种卡 {product} 的静态规格不一致")
