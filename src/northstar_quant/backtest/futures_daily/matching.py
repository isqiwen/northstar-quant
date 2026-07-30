"""逐日撮合、费用、成交量与保证金计算。"""

from __future__ import annotations

import math

from northstar_quant.backtest.futures_daily.models import (
    FuturesDailyBar,
    FuturesInstrumentSpec,
    FuturesTrade,
    PositionState,
)


def open_position(
    day,
    instrument_id,
    signed_qty,
    price,
    reason,
    bars,
    specs,
    positions,
    cash,
    result,
    used_volume,
    participation,
):
    """按当日规则模拟开仓，并返回更新后的现金与成交手数。"""

    if signed_qty == 0:
        return cash, 0
    side = "BUY" if signed_qty > 0 else "SELL"
    requested = abs(signed_qty)
    filled = fillable_qty(
        day,
        instrument_id,
        side,
        price,
        requested,
        bars,
        specs,
        result,
        used_volume,
        participation,
    )
    if not filled:
        return cash, 0
    signed_fill = filled if signed_qty > 0 else -filled
    spec, bar = specs[instrument_id], bars[instrument_id]
    fill = fill_price(price, side=side, spec=spec)
    commission = open_commission(filled, fill, spec, bar)
    current = positions.get(instrument_id)
    if current is None:
        positions[instrument_id] = PositionState(
            qty=signed_fill,
            settlement_price=fill,
            stop_price=None,
            target_price=None,
            opened_today_qty=filled,
        )
    elif current.qty * signed_fill > 0:
        total = abs(current.qty) + filled
        current.settlement_price = (
            abs(current.qty) * current.settlement_price + filled * fill
        ) / total
        current.qty += signed_fill
        current.opened_today_qty += filled
    else:
        raise RuntimeError("状态机内部错误：开仓方向与既有持仓冲突")
    result.trades.append(
        FuturesTrade(day, instrument_id, side, filled, fill, commission, reason)
    )
    used_volume[instrument_id] = used_volume.get(instrument_id, 0) + filled
    return cash - commission, filled


def close_position(
    day,
    instrument_id,
    qty,
    price,
    reason,
    bars,
    specs,
    positions,
    cash,
    result,
    used_volume,
    participation,
):
    """按平昨/平今数量拆分费用后模拟平仓。"""

    position = positions[instrument_id]
    if qty <= 0 or qty > abs(position.qty):
        raise ValueError("平仓手数超出持仓")
    side = "SELL" if position.qty > 0 else "BUY"
    filled = fillable_qty(
        day,
        instrument_id,
        side,
        price,
        qty,
        bars,
        specs,
        result,
        used_volume,
        participation,
    )
    if not filled:
        return cash, 0
    spec, bar = specs[instrument_id], bars[instrument_id]
    fill = fill_price(price, side=side, spec=spec)
    direction = 1 if position.qty > 0 else -1
    cash += direction * filled * (fill - position.settlement_price) * spec.multiplier
    close_today_qty = min(filled, position.opened_today_qty)
    close_old_qty = filled - close_today_qty
    commission = close_commission(
        close_old_qty,
        close_today_qty,
        fill,
        spec,
        bar,
    )
    cash -= commission
    position.qty += -filled if position.qty > 0 else filled
    position.opened_today_qty -= close_today_qty
    result.trades.append(
        FuturesTrade(day, instrument_id, side, filled, fill, commission, reason)
    )
    used_volume[instrument_id] = used_volume.get(instrument_id, 0) + filled
    if position.qty == 0:
        del positions[instrument_id]
    return cash, filled


def fillable_qty(
    day,
    instrument_id,
    side,
    price,
    requested,
    bars,
    specs,
    result,
    used_volume,
    participation,
) -> int:
    """应用涨跌停和成交量参与率，返回可模拟成交的手数。"""

    reason = order_block_reason(side, price, bars[instrument_id], specs[instrument_id])
    if reason:
        result.rejected_targets.append(f"{day}/{instrument_id}: {reason}")
        return 0
    available = available_volume(
        bars[instrument_id],
        used_volume.get(instrument_id, 0),
        participation,
    )
    filled = min(requested, available)
    if filled < requested:
        result.rejected_targets.append(
            f"{day}/{instrument_id}: 成交量参与率限制，请求 {requested} 手，成交 {filled} 手"
        )
    return filled


def available_volume(
    bar: FuturesDailyBar,
    used: int,
    participation: float,
) -> int:
    return max(math.floor(bar.volume * participation) - used, 0)


def order_block_reason(
    side: str,
    reference_price: float,
    bar: FuturesDailyBar,
    spec: FuturesInstrumentSpec,
) -> str | None:
    fill = fill_price(reference_price, side=side, spec=spec)
    if side == "BUY" and (reference_price >= bar.upper_limit or fill > bar.upper_limit):
        return "买单触及涨停，无法可靠假设成交"
    if side == "SELL" and (reference_price <= bar.lower_limit or fill < bar.lower_limit):
        return "卖单触及跌停，无法可靠假设成交"
    return None


def fill_price(
    price: float,
    *,
    side: str,
    spec: FuturesInstrumentSpec,
) -> float:
    return price + spec.tick_size * spec.slippage_ticks * (
        1 if side == "BUY" else -1
    )


def open_commission(qty, price, spec, bar) -> float:
    return qty * (
        bar.commission_open_per_lot
        + price * spec.multiplier * bar.commission_open_rate
    )


def close_commission(old_qty, today_qty, price, spec, bar) -> float:
    old_cost = old_qty * (
        bar.commission_close_per_lot
        + price * spec.multiplier * bar.commission_close_rate
    )
    today_cost = today_qty * (
        bar.commission_close_today_per_lot
        + price * spec.multiplier * bar.commission_close_today_rate
    )
    return old_cost + today_cost


def target_margin_is_affordable(
    instrument_id,
    target_qty,
    bars,
    specs,
    positions,
    cash,
) -> bool:
    """以开盘盯市权益和保守交易成本检查目标保证金。"""

    projected = {
        key: PositionState(
            value.qty,
            value.settlement_price,
            value.stop_price,
            value.target_price,
            value.opened_today_qty,
        )
        for key, value in positions.items()
    }
    current_qty = projected[instrument_id].qty if instrument_id in projected else 0
    if target_qty:
        projected[instrument_id] = PositionState(
            target_qty,
            bars[instrument_id].open,
            None,
            None,
        )
    else:
        projected.pop(instrument_id, None)
    traded_qty = abs(target_qty - current_qty)
    bar, spec = bars[instrument_id], specs[instrument_id]
    estimated_cost = traded_qty * (
        max(
            bar.commission_open_per_lot,
            bar.commission_close_per_lot,
            bar.commission_close_today_per_lot,
        )
        + bar.open
        * spec.multiplier
        * max(
            bar.commission_open_rate,
            bar.commission_close_rate,
            bar.commission_close_today_rate,
        )
        + spec.tick_size * spec.slippage_ticks * spec.multiplier
    )
    projected_equity = equity_marked_at_open(bars, specs, positions, cash) - estimated_cost
    return (
        margin_required(bars, specs, projected, price_field="open")
        <= projected_equity + 1e-8
    )


def rollover_margin_is_affordable(
    old_id,
    new_id,
    bars,
    specs,
    positions,
    cash,
) -> bool:
    """以两腿完整成交的费用和滑点检查换月后保证金。"""

    projected = {
        key: PositionState(
            value.qty,
            value.settlement_price,
            value.stop_price,
            value.target_price,
            value.opened_today_qty,
        )
        for key, value in positions.items()
    }
    qty = projected.pop(old_id).qty
    projected[new_id] = PositionState(qty, bars[new_id].open, None, None)
    old_bar, new_bar = bars[old_id], bars[new_id]
    old_spec, new_spec = specs[old_id], specs[new_id]
    trading_cost = close_commission(
        abs(qty),
        0,
        old_bar.open,
        old_spec,
        old_bar,
    ) + open_commission(abs(qty), new_bar.open, new_spec, new_bar)
    trading_cost += abs(qty) * (
        old_spec.tick_size * old_spec.slippage_ticks * old_spec.multiplier
        + new_spec.tick_size * new_spec.slippage_ticks * new_spec.multiplier
    )
    projected_equity = equity_marked_at_open(bars, specs, positions, cash) - trading_cost
    return (
        margin_required(bars, specs, projected, price_field="open")
        <= projected_equity + 1e-8
    )


def equity_marked_at_open(bars, specs, positions, cash) -> float:
    """把现有持仓按当日第一交易时段开盘价盯市。"""

    equity = float(cash)
    for instrument_id, position in positions.items():
        if instrument_id not in bars:
            raise ValueError(f"持仓合约缺少日线（开盘）：{instrument_id}")
        equity += (
            position.qty
            * (bars[instrument_id].open - position.settlement_price)
            * specs[instrument_id].multiplier
        )
    return equity


def margin_required(bars, specs, positions, *, price_field: str) -> float:
    """按给定价格字段和当日动态保证金率计算占用。"""

    return sum(
        abs(position.qty)
        * getattr(bars[instrument_id], price_field)
        * specs[instrument_id].multiplier
        * bars[instrument_id].margin_rate
        for instrument_id, position in positions.items()
    )
