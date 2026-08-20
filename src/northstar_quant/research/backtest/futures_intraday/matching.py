"""分钟盘口撮合、持仓、费用与保证金计算。"""

from __future__ import annotations

from datetime import date, datetime
import math

from northstar_quant.research.backtest.futures_daily.models import (
    FuturesInstrumentSpec,
    PositionState,
)
from northstar_quant.research.backtest.futures_intraday.models import (
    FuturesIntradayBar,
    FuturesIntradayTrade,
    FuturesReplayResult,
    OrderOffset,
    OrderSide,
    OrderStatus,
    OrderType,
    ReplayOrder,
    ReplayOrderRequest,
)

TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.EXPIRED,
    OrderStatus.REJECTED,
}


def match_order(
    order: ReplayOrder,
    bar: FuturesIntradayBar,
    specs: dict[str, FuturesInstrumentSpec],
    positions: dict[str, PositionState],
    latest_bars: dict[str, FuturesIntradayBar],
    cash: float,
    result: FuturesReplayResult,
    used_volume: dict[str, int],
    participation: float,
    queue_ratio: float,
) -> float:
    """在一根分钟线上尝试撮合一张委托。"""

    request = order.request
    instrument_id = request.instrument_id.upper()
    spec = specs[instrument_id]
    capacity, fill_price = _match_capacity_and_price(
        order,
        bar,
        spec,
        used_volume.get(instrument_id, 0),
        participation,
        queue_ratio,
    )
    if capacity <= 0 or fill_price is None:
        order.updated_at = bar.timestamp
        return cash
    requested_fill = min(order.remaining_qty, capacity)
    if request.offset == OrderOffset.CLOSE:
        cash, filled, commission, error = _apply_close_fill(
            request,
            requested_fill,
            fill_price,
            bar,
            spec,
            positions,
            cash,
        )
    else:
        cash, filled, commission, error = _apply_open_fill(
            request,
            requested_fill,
            fill_price,
            bar,
            spec,
            positions,
            latest_bars,
            specs,
            cash,
        )
    if error:
        finish_order(order, OrderStatus.REJECTED, bar.timestamp, error)
        result.rejected_orders.append(
            f"{bar.timestamp.isoformat()}/{request.order_id}: {error}"
        )
        return cash
    if filled <= 0:
        return cash

    previous_notional = (order.average_fill_price or 0.0) * order.filled_qty
    order.filled_qty += filled
    order.average_fill_price = (
        previous_notional + fill_price * filled
    ) / order.filled_qty
    order.commission += commission
    order.updated_at = bar.timestamp
    order.status = (
        OrderStatus.FILLED
        if order.remaining_qty == 0
        else OrderStatus.PARTIALLY_FILLED
    )
    used_volume[instrument_id] = used_volume.get(instrument_id, 0) + filled
    result.trades.append(
        FuturesIntradayTrade(
            trading_day=bar.trading_day,
            timestamp=bar.timestamp,
            order_id=request.order_id,
            instrument_id=instrument_id,
            side=request.side.value,
            offset=request.offset.value,
            qty=filled,
            price=fill_price,
            commission=commission,
            reason=request.reason,
        )
    )
    return cash


def _match_capacity_and_price(
    order: ReplayOrder,
    bar: FuturesIntradayBar,
    spec: FuturesInstrumentSpec,
    used_volume: int,
    participation: float,
    queue_ratio: float,
) -> tuple[int, float | None]:
    request = order.request
    traded_capacity = max(math.floor(bar.volume * participation) - used_volume, 0)
    if traded_capacity <= 0:
        return 0, None
    is_buy = request.side == OrderSide.BUY
    book_price = bar.ask_price if is_buy else bar.bid_price
    book_volume = bar.ask_volume if is_buy else bar.bid_volume
    slippage = spec.tick_size * spec.slippage_ticks * (1 if is_buy else -1)

    marketable = request.order_type == OrderType.MARKET or (
        request.limit_price is not None
        and (
            (is_buy and request.limit_price >= bar.ask_price)
            or (not is_buy and request.limit_price <= bar.bid_price)
        )
    )
    if marketable:
        if book_volume <= 0:
            return 0, None
        price = book_price + slippage
        if not bar.lower_limit <= price <= bar.upper_limit:
            return 0, None
        if request.limit_price is not None and (
            (is_buy and price > request.limit_price)
            or (not is_buy and price < request.limit_price)
        ):
            return 0, None
        return min(traded_capacity, math.floor(book_volume)), price

    limit_price = request.limit_price
    if limit_price is None:
        return 0, None
    touched = bar.low <= limit_price if is_buy else bar.high >= limit_price
    if not touched or not bar.lower_limit <= limit_price <= bar.upper_limit:
        return 0, None
    if order.queue_ahead_remaining is None:
        order.queue_ahead_remaining = math.floor(bar.volume * queue_ratio)
    queue_consumed = min(order.queue_ahead_remaining, math.floor(bar.volume))
    order.queue_ahead_remaining -= queue_consumed
    volume_after_queue = max(math.floor(bar.volume) - queue_consumed, 0)
    return min(traded_capacity, volume_after_queue), limit_price


def _apply_open_fill(
    request: ReplayOrderRequest,
    qty: int,
    price: float,
    bar: FuturesIntradayBar,
    spec: FuturesInstrumentSpec,
    positions: dict[str, PositionState],
    latest_bars: dict[str, FuturesIntradayBar],
    specs: dict[str, FuturesInstrumentSpec],
    cash: float,
) -> tuple[float, int, float, str | None]:
    instrument_id = request.instrument_id.upper()
    signed = qty if request.side == OrderSide.BUY else -qty
    current = positions.get(instrument_id)
    if current is not None and current.qty * signed < 0:
        return cash, 0, 0.0, "开仓方向与现有持仓冲突，必须先完成平仓"
    current_abs = abs(current.qty) if current else 0
    allowed_by_limit = max(bar.max_position_lots - current_abs, 0)
    equity = account_equity(latest_bars, specs, positions, cash)
    margin = margin_required(bar.trading_day, latest_bars, specs, positions)
    commission_per_lot = _open_commission(1, price, spec, bar)
    required_per_lot = price * spec.multiplier * bar.margin_rate + commission_per_lot
    allowed_by_margin = max(math.floor((equity - margin) / required_per_lot), 0)
    filled = min(qty, allowed_by_limit, allowed_by_margin)
    if filled <= 0:
        return cash, 0, 0.0, "保证金或动态限仓不足，开仓失败"
    signed_fill = filled if request.side == OrderSide.BUY else -filled
    commission = _open_commission(filled, price, spec, bar)
    if current is None:
        positions[instrument_id] = PositionState(
            qty=signed_fill,
            settlement_price=price,
            stop_price=None,
            target_price=None,
            opened_today_qty=filled,
        )
    else:
        total = abs(current.qty) + filled
        current.settlement_price = (
            abs(current.qty) * current.settlement_price + filled * price
        ) / total
        current.qty += signed_fill
        current.opened_today_qty += filled
    return cash - commission, filled, commission, None


def _apply_close_fill(
    request: ReplayOrderRequest,
    qty: int,
    price: float,
    bar: FuturesIntradayBar,
    spec: FuturesInstrumentSpec,
    positions: dict[str, PositionState],
    cash: float,
) -> tuple[float, int, float, str | None]:
    instrument_id = request.instrument_id.upper()
    position = positions.get(instrument_id)
    expected_side = (
        OrderSide.SELL
        if position is not None and position.qty > 0
        else OrderSide.BUY
    )
    if position is None or request.side != expected_side:
        return cash, 0, 0.0, "平仓方向没有对应持仓"
    filled = min(qty, abs(position.qty))
    direction = 1 if position.qty > 0 else -1
    cash += direction * filled * (price - position.settlement_price) * spec.multiplier
    close_today = min(filled, position.opened_today_qty)
    close_old = filled - close_today
    commission = _close_commission(close_old, close_today, price, spec, bar)
    cash -= commission
    position.qty += -filled if position.qty > 0 else filled
    position.opened_today_qty -= close_today
    if position.qty == 0:
        del positions[instrument_id]
    return cash, filled, commission, None


def account_equity(
    latest_bars: dict[str, FuturesIntradayBar],
    specs: dict[str, FuturesInstrumentSpec],
    positions: dict[str, PositionState],
    cash: float,
) -> float:
    """按最新分钟收盘价计算账户权益。"""

    equity = float(cash)
    for instrument_id, position in positions.items():
        bar = latest_bars.get(instrument_id)
        if bar is None:
            raise ValueError(f"持仓合约缺少最新分钟线：{instrument_id}")
        equity += (
            position.qty
            * (bar.close - position.settlement_price)
            * specs[instrument_id].multiplier
        )
    return equity


def margin_required(
    trading_day: date,
    latest_bars: dict[str, FuturesIntradayBar],
    specs: dict[str, FuturesInstrumentSpec],
    positions: dict[str, PositionState],
) -> float:
    """按最新价或日终结算价计算动态保证金占用。"""

    margin = 0.0
    for instrument_id, position in positions.items():
        bar = latest_bars.get(instrument_id)
        if bar is None:
            raise ValueError(f"持仓合约缺少保证金行情：{instrument_id}")
        price = (
            bar.settlement
            if bar.trading_day == trading_day and bar.is_trading_day_end
            else bar.close
        )
        margin += (
            abs(position.qty)
            * price
            * specs[instrument_id].multiplier
            * bar.margin_rate
        )
    return margin


def _open_commission(
    qty: int,
    price: float,
    spec: FuturesInstrumentSpec,
    bar: FuturesIntradayBar,
) -> float:
    return qty * (
        bar.commission_open_per_lot
        + price * spec.multiplier * bar.commission_open_rate
    )


def _close_commission(
    old_qty: int,
    today_qty: int,
    price: float,
    spec: FuturesInstrumentSpec,
    bar: FuturesIntradayBar,
) -> float:
    return old_qty * (
        bar.commission_close_per_lot
        + price * spec.multiplier * bar.commission_close_rate
    ) + today_qty * (
        bar.commission_close_today_per_lot
        + price * spec.multiplier * bar.commission_close_today_rate
    )


def finish_order(
    order: ReplayOrder,
    status: OrderStatus,
    timestamp: datetime,
    message: str,
) -> None:
    """把委托推进到一个明确终态。"""

    order.status = status
    order.updated_at = timestamp
    order.message = message
