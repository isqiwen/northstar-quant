"""国内期货实际合约逐日持仓与撮合引擎。

本模块只接受具体合约和逐日动态规则。信号在决策日生成，按后续交易日第一交易时段
开盘执行；有夜盘时，输入日线的 open 必须是该交易日夜盘开盘。日内止损/止盈使用
保守 OHLC 顺序，同日同时触及时优先止损。
"""

from __future__ import annotations

from datetime import date
import math
from typing import Iterable

from northstar_quant.backtest.futures_daily.matching import (
    available_volume as _available_volume,
    close_position as _close,
    equity_marked_at_open as _equity_marked_at_open,
    margin_required as _margin_required,
    open_position as _open,
    order_block_reason as _order_block_reason,
    rollover_margin_is_affordable as _rollover_margin_is_affordable,
    target_margin_is_affordable as _target_margin_is_affordable,
)
from northstar_quant.backtest.futures_daily.models import (
    FuturesDailyBacktestResult,
    FuturesDailyBar,
    FuturesInstrumentSpec,
    FuturesRollover,
    FuturesTarget,
    FuturesWeightTarget,
    PositionState,
)
from northstar_quant.config.product_cards import load_product_cards


def run_daily_futures_backtest(
    *,
    bars: Iterable[FuturesDailyBar],
    instrument_specs: Iterable[FuturesInstrumentSpec],
    targets: Iterable[FuturesTarget] = (),
    weight_targets: Iterable[FuturesWeightTarget] = (),
    rollovers: Iterable[FuturesRollover] = (),
    initial_cash: float = 1_000_000.0,
    trading_calendar: Iterable[date] | None = None,
    execution_delay_sessions: int = 1,
    max_volume_participation: float = 1.0,
) -> FuturesDailyBacktestResult:
    """运行实际合约逐日状态机。

    每日顺序固定为：换月、延迟目标、日内保护单、结算盯市、保证金检查。成交受动态
    手续费、涨跌停、限仓和成交量参与率约束；无法确认成交时保守拒单。日线已完整包含
    夜盘与日盘，但 OHLC 无法还原逐笔路径，因此该模型仍不等价于逐笔撮合。
    """

    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash 必须是正有限数")
    if execution_delay_sessions < 1:
        raise ValueError("execution_delay_sessions 必须大于等于 1")
    if not 0 < max_volume_participation <= 1:
        raise ValueError("max_volume_participation 必须位于 (0, 1]")

    specs = _index_specs(instrument_specs)
    _validate_instrument_specs_against_product_cards(specs)
    by_day = _index_bars(bars, specs)
    sessions = sorted(trading_calendar or by_day)
    if sessions != sorted(set(sessions)) or not sessions:
        raise ValueError("交易日历必须非空、无重复且升序")
    if any(day not in sessions for day in by_day):
        raise ValueError("日线包含不在交易日历中的日期")
    quantity_schedule = _schedule_targets(targets, sessions, execution_delay_sessions)
    weight_schedule = _schedule_targets(weight_targets, sessions, execution_delay_sessions)
    rollover_schedule = _schedule_rollovers(rollovers, sessions)
    cash = float(initial_cash)
    positions: dict[str, PositionState] = {}
    result = FuturesDailyBacktestResult(final_equity=cash)

    for day in sessions:
        for position in positions.values():
            position.opened_today_qty = 0
        day_bars = by_day.get(day, {})
        used_volume: dict[str, int] = {}
        cash = _process_rollovers(
            day,
            day_bars,
            specs,
            positions,
            cash,
            rollover_schedule.get(day, ()),
            result,
            used_volume,
            max_volume_participation,
        )
        cash = _process_weight_targets(
            day,
            day_bars,
            specs,
            positions,
            cash,
            weight_schedule.get(day, ()),
            result,
            used_volume,
            max_volume_participation,
        )
        cash = _process_targets(
            day,
            day_bars,
            specs,
            positions,
            cash,
            quantity_schedule.get(day, ()),
            result,
            used_volume,
            max_volume_participation,
        )
        cash = _process_protective_exits(
            day,
            day_bars,
            specs,
            positions,
            cash,
            result,
            used_volume,
            max_volume_participation,
        )
        cash = _settle_and_apply_margin(
            day,
            day_bars,
            specs,
            positions,
            cash,
            result,
            used_volume,
            max_volume_participation,
        )
        margin = _margin_required(day_bars, specs, positions, price_field="settlement")
        result.equity_curve.append(
            {
                "date": day.isoformat(),
                "equity": cash,
                "margin": margin,
                "available_funds": cash - margin,
            }
        )
    result.final_equity = cash
    return result


def _index_specs(
    instrument_specs: Iterable[FuturesInstrumentSpec],
) -> dict[str, FuturesInstrumentSpec]:
    items = tuple(instrument_specs)
    specs = {item.instrument_id.upper(): item for item in items}
    if not specs:
        raise ValueError("至少需要一个具体期货合约规格")
    if len(specs) != len(items):
        raise ValueError("具体期货合约规格 instrument_id 不能重复")
    return specs


def _index_bars(
    bars: Iterable[FuturesDailyBar],
    specs: dict[str, FuturesInstrumentSpec],
) -> dict[date, dict[str, FuturesDailyBar]]:
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
    return by_day


def _schedule_targets(targets, sessions: list[date], delay: int):
    index = {day: offset for offset, day in enumerate(sessions)}
    scheduled: dict[date, list] = {}
    for target in targets:
        if target.decision_day not in index:
            raise ValueError(f"信号决策日不在交易日历中：{target.decision_day}")
        execute_offset = index[target.decision_day] + delay
        if execute_offset < len(sessions):
            scheduled.setdefault(sessions[execute_offset], []).append(target)
    return scheduled


def _schedule_rollovers(
    rollovers: Iterable[FuturesRollover],
    sessions: list[date],
) -> dict[date, list[FuturesRollover]]:
    scheduled: dict[date, list[FuturesRollover]] = {}
    for rollover in rollovers:
        if rollover.trading_day not in sessions:
            raise ValueError(f"换月日不在交易日历中：{rollover.trading_day}")
        scheduled.setdefault(rollover.trading_day, []).append(rollover)
    return scheduled


def _process_weight_targets(
    day,
    bars,
    specs,
    positions,
    cash,
    targets,
    result,
    used_volume,
    participation,
) -> float:
    if not targets:
        return cash
    equity = _equity_marked_at_open(bars, specs, positions, cash)
    quantity_targets: list[FuturesTarget] = []
    for target in targets:
        instrument_id = target.instrument_id.upper()
        if instrument_id not in bars or instrument_id not in specs:
            raise ValueError(f"权重目标执行日缺少具体合约开盘日线：{instrument_id}")
        bar = bars[instrument_id]
        per_lot_margin = bar.open * specs[instrument_id].multiplier * bar.margin_rate
        qty = math.floor(abs(equity * target.target_weight) / per_lot_margin)
        qty = min(qty, bar.max_position_lots)
        quantity_targets.append(
            FuturesTarget(
                decision_day=target.decision_day,
                instrument_id=instrument_id,
                target_qty=qty if target.target_weight >= 0 else -qty,
            )
        )
    return _process_targets(
        day,
        bars,
        specs,
        positions,
        cash,
        quantity_targets,
        result,
        used_volume,
        participation,
    )


def _process_rollovers(
    day,
    bars,
    specs,
    positions,
    cash,
    rollovers,
    result,
    used_volume,
    participation,
) -> float:
    for rollover in rollovers:
        old_id = rollover.from_instrument_id.upper()
        new_id = rollover.to_instrument_id.upper()
        if old_id not in specs or new_id not in specs or old_id not in bars or new_id not in bars:
            raise ValueError(f"换月缺少合约规格或开盘日线：{old_id} → {new_id}")
        if specs[old_id].product != specs[new_id].product:
            raise ValueError(f"换月前后必须属于同一品种：{old_id} → {new_id}")
        position = positions.get(old_id)
        if position is None:
            continue
        if new_id in positions:
            raise ValueError(f"换月新合约已存在持仓，无法安全合并：{new_id}")
        qty = position.qty
        close_side = "SELL" if qty > 0 else "BUY"
        open_side = "BUY" if qty > 0 else "SELL"
        for instrument_id, side in ((old_id, close_side), (new_id, open_side)):
            reason = _order_block_reason(
                side,
                bars[instrument_id].open,
                bars[instrument_id],
                specs[instrument_id],
            )
            available = _available_volume(
                bars[instrument_id],
                used_volume.get(instrument_id, 0),
                participation,
            )
            if reason or available < abs(qty):
                detail = reason or "成交量参与率不足"
                raise ValueError(f"换月无法完整成交：{old_id} → {new_id}；{detail}")
        if not _rollover_margin_is_affordable(
            old_id,
            new_id,
            bars,
            specs,
            positions,
            cash,
        ):
            raise ValueError(f"换月后保证金或交易成本不足：{old_id} → {new_id}")
        stop, target = position.stop_price, position.target_price
        shift = bars[new_id].open - bars[old_id].open
        cash, closed = _close(
            day,
            old_id,
            abs(qty),
            bars[old_id].open,
            "roll_close",
            bars,
            specs,
            positions,
            cash,
            result,
            used_volume,
            participation,
        )
        cash, opened = _open(
            day,
            new_id,
            qty,
            bars[new_id].open,
            "roll_open",
            bars,
            specs,
            positions,
            cash,
            result,
            used_volume,
            participation,
        )
        if closed != abs(qty) or opened != abs(qty):
            raise RuntimeError("状态机内部错误：换月预检后未完整成交")
        positions[new_id].stop_price = stop + shift if stop is not None else None
        positions[new_id].target_price = target + shift if target is not None else None
    return cash


def _process_targets(
    day,
    bars,
    specs,
    positions,
    cash,
    targets,
    result,
    used_volume,
    participation,
) -> float:
    for target in targets:
        instrument_id = target.instrument_id.upper()
        if instrument_id not in specs or instrument_id not in bars:
            raise ValueError(f"目标执行日缺少具体合约开盘日线：{instrument_id}")
        bar = bars[instrument_id]
        _validate_target_protective_prices(target, bar.open)
        if abs(target.target_qty) > bar.max_position_lots:
            result.rejected_targets.append(
                f"{day}/{instrument_id}: 目标手数 {target.target_qty} 超过当日限仓 "
                f"{bar.max_position_lots}"
            )
            continue
        if not _target_margin_is_affordable(
            instrument_id,
            target.target_qty,
            bars,
            specs,
            positions,
            cash,
        ):
            result.rejected_targets.append(
                f"{day}/{instrument_id}: 保证金不足，目标手数 {target.target_qty} 被拒绝"
            )
            continue

        current_qty = positions[instrument_id].qty if instrument_id in positions else 0
        if current_qty and target.target_qty and (current_qty > 0) != (target.target_qty > 0):
            cash, closed = _close(
                day,
                instrument_id,
                abs(current_qty),
                bar.open,
                "target_close",
                bars,
                specs,
                positions,
                cash,
                result,
                used_volume,
                participation,
            )
            if closed < abs(current_qty):
                continue

        current_qty = positions[instrument_id].qty if instrument_id in positions else 0
        delta = target.target_qty - current_qty
        if delta < 0 and current_qty > 0:
            cash, _ = _close(
                day,
                instrument_id,
                min(-delta, current_qty),
                bar.open,
                "target_reduce",
                bars,
                specs,
                positions,
                cash,
                result,
                used_volume,
                participation,
            )
        elif delta > 0 and current_qty < 0:
            cash, _ = _close(
                day,
                instrument_id,
                min(delta, -current_qty),
                bar.open,
                "target_reduce",
                bars,
                specs,
                positions,
                cash,
                result,
                used_volume,
                participation,
            )

        remaining = target.target_qty - (
            positions[instrument_id].qty if instrument_id in positions else 0
        )
        if remaining:
            cash, _ = _open(
                day,
                instrument_id,
                remaining,
                bar.open,
                "target_open",
                bars,
                specs,
                positions,
                cash,
                result,
                used_volume,
                participation,
            )
        if instrument_id in positions:
            positions[instrument_id].stop_price = target.stop_price
            positions[instrument_id].target_price = target.target_price
    return cash


def _process_protective_exits(
    day,
    bars,
    specs,
    positions,
    cash,
    result,
    used_volume,
    participation,
) -> float:
    for instrument_id in list(positions):
        if instrument_id not in bars:
            raise ValueError(f"持仓合约缺少日线，无法安全盯市：{instrument_id}")
        position, bar = positions[instrument_id], bars[instrument_id]
        exit_price, reason = _protective_exit_price(position, bar)
        if exit_price is not None:
            cash, _ = _close(
                day,
                instrument_id,
                abs(position.qty),
                exit_price,
                reason,
                bars,
                specs,
                positions,
                cash,
                result,
                used_volume,
                participation,
            )
    return cash


def _protective_exit_price(
    position: PositionState,
    bar: FuturesDailyBar,
) -> tuple[float | None, str]:
    stop, target = position.stop_price, position.target_price
    if position.qty > 0:
        stop_hit = stop is not None and (bar.open <= stop or bar.low <= stop)
        target_hit = target is not None and (bar.open >= target or bar.high >= target)
        if stop_hit:
            assert stop is not None
            return (bar.open if bar.open <= stop else stop), "stop_loss"
        if target_hit:
            assert target is not None
            return (bar.open if bar.open >= target else target), "take_profit"
    else:
        stop_hit = stop is not None and (bar.open >= stop or bar.high >= stop)
        target_hit = target is not None and (bar.open <= target or bar.low <= target)
        if stop_hit:
            assert stop is not None
            return (bar.open if bar.open >= stop else stop), "stop_loss"
        if target_hit:
            assert target is not None
            return (bar.open if bar.open <= target else target), "take_profit"
    return None, ""


def _settle_and_apply_margin(
    day,
    bars,
    specs,
    positions,
    cash,
    result,
    used_volume,
    participation,
) -> float:
    for instrument_id, position in list(positions.items()):
        if instrument_id not in bars:
            raise ValueError(f"持仓合约缺少结算日线：{instrument_id}")
        bar, spec = bars[instrument_id], specs[instrument_id]
        cash += (
            position.qty
            * (bar.settlement - position.settlement_price)
            * spec.multiplier
        )
        position.settlement_price = bar.settlement

    required = _margin_required(bars, specs, positions, price_field="settlement")
    if required <= cash + 1e-8:
        return cash
    for instrument_id in list(positions):
        cash, _ = _close(
            day,
            instrument_id,
            abs(positions[instrument_id].qty),
            bars[instrument_id].settlement,
            "margin_call",
            bars,
            specs,
            positions,
            cash,
            result,
            used_volume,
            participation,
        )
    remaining_margin = _margin_required(
        bars,
        specs,
        positions,
        price_field="settlement",
    )
    if remaining_margin > cash + 1e-8:
        result.rejected_targets.append(
            f"{day}: 保证金追缴平仓未能完整成交，剩余保证金 {remaining_margin:.2f}"
        )
    return cash


def _validate_target_protective_prices(
    target: FuturesTarget,
    execution_price: float,
) -> None:
    if target.target_qty > 0:
        if target.stop_price is not None and target.stop_price >= execution_price:
            raise ValueError("多头初始止损价必须低于执行价")
        if target.target_price is not None and target.target_price <= execution_price:
            raise ValueError("多头初始止盈价必须高于执行价")
    elif target.target_qty < 0:
        if target.stop_price is not None and target.stop_price <= execution_price:
            raise ValueError("空头初始止损价必须高于执行价")
        if target.target_price is not None and target.target_price >= execution_price:
            raise ValueError("空头初始止盈价必须低于执行价")


def _validate_instrument_specs_against_product_cards(
    specs: dict[str, FuturesInstrumentSpec],
) -> None:
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
