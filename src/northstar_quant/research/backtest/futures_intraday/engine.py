"""实际期货合约分钟订单回放编排。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
import math

from northstar_quant.research.backtest.futures_daily.models import (
    FuturesInstrumentSpec,
    PositionState,
)
from northstar_quant.research.backtest.futures_intraday.matching import (
    TERMINAL_ORDER_STATUSES,
    finish_order,
    margin_required,
    match_order,
)
from northstar_quant.research.backtest.futures_intraday.models import (
    FuturesIntradayBar,
    FuturesReplayResult,
    IntradayWeightTarget,
    OrderOffset,
    OrderSide,
    OrderStatus,
    ReplayCancellation,
    ReplayOrder,
    ReplayOrderRequest,
)
from northstar_quant.research.backtest.futures_intraday.orders import (
    apply_cancellations,
    build_target_orders,
    index_cancellations,
    index_orders,
    schedule_weight_targets,
)
from northstar_quant.data_platform.contracts.product_cards import load_product_cards


def run_intraday_futures_replay(
    *,
    bars: Iterable[FuturesIntradayBar],
    instrument_specs: Iterable[FuturesInstrumentSpec],
    weight_targets: Iterable[IntradayWeightTarget] = (),
    orders: Iterable[ReplayOrderRequest] = (),
    cancellations: Iterable[ReplayCancellation] = (),
    initial_cash: float = 1_000_000.0,
    max_volume_participation: float = 0.05,
    queue_ahead_ratio: float = 0.0,
    generated_order_ttl_bars: int = 5,
) -> FuturesReplayResult:
    """按分钟盘口和成交量回放委托、成交、结算及保证金账户。

    外部委托只能从 ``submitted_at`` 之后的分钟参与撮合；画像目标在上一交易日已经
    确定，因此会在执行交易日首根实际合约分钟线上进入市场。所有被动限价成交均先
    扣除排队量，所有开仓部分成交均重新检查动态保证金与限仓。
    """

    _validate_run_settings(
        initial_cash=initial_cash,
        max_volume_participation=max_volume_participation,
        queue_ahead_ratio=queue_ahead_ratio,
        generated_order_ttl_bars=generated_order_ttl_bars,
    )
    specs = _index_specs(instrument_specs)
    indexed_bars = _index_bars(bars, specs)
    targets_by_timestamp = schedule_weight_targets(
        weight_targets,
        indexed_bars,
        specs,
    )
    order_states = index_orders(orders, specs)
    cancellation_schedule = index_cancellations(cancellations, order_states)
    missing_cancellation_timestamps = sorted(
        set(cancellation_schedule).difference(indexed_bars)
    )
    if missing_cancellation_timestamps:
        raise ValueError(
            "撤单时间必须对应行情 timestamp："
            + ", ".join(item.isoformat() for item in missing_cancellation_timestamps)
        )

    result = FuturesReplayResult(final_equity=float(initial_cash))
    positions: dict[str, PositionState] = {}
    latest_bars: dict[str, FuturesIntradayBar] = {}
    cash = float(initial_cash)
    last_timestamp_by_day = _last_timestamp_by_day(indexed_bars)
    first_timestamp_by_day = _first_timestamp_by_day(indexed_bars)

    for timestamp, current_bars in indexed_bars.items():
        trading_day = _single_trading_day(timestamp, current_bars)
        if timestamp == first_timestamp_by_day[trading_day]:
            for position in positions.values():
                position.opened_today_qty = 0

        latest_bars.update(current_bars)
        generated = build_target_orders(
            timestamp,
            targets_by_timestamp.get(timestamp, ()),
            current_bars,
            latest_bars,
            specs,
            positions,
            cash,
            generated_order_ttl_bars,
            existing_order_ids=set(order_states),
        )
        for request in generated:
            order_states[request.order_id] = ReplayOrder(request=request)

        apply_cancellations(
            timestamp,
            cancellation_schedule.get(timestamp, ()),
            order_states,
        )
        cash = _match_current_bars(
            timestamp,
            current_bars,
            latest_bars,
            specs,
            positions,
            order_states,
            cash,
            result,
            max_volume_participation,
            queue_ahead_ratio,
        )

        if timestamp == last_timestamp_by_day[trading_day]:
            cash = _settle_day(
                trading_day,
                timestamp,
                latest_bars,
                specs,
                positions,
                cash,
                result,
                order_states,
                max_volume_participation,
            )
            margin = margin_required(
                trading_day,
                latest_bars,
                specs,
                positions,
            )
            result.equity_curve.append(
                {
                    "date": trading_day.isoformat(),
                    "equity": cash,
                    "margin": margin,
                    "available_funds": cash - margin,
                }
            )

    final_timestamp = next(reversed(indexed_bars))
    for order in order_states.values():
        if order.status not in TERMINAL_ORDER_STATUSES:
            finish_order(order, OrderStatus.EXPIRED, final_timestamp, "回放数据结束")
    result.orders = sorted(
        order_states.values(),
        key=lambda item: (item.request.submitted_at, item.request.order_id),
    )
    result.final_equity = cash
    return result


def _validate_run_settings(
    *,
    initial_cash: float,
    max_volume_participation: float,
    queue_ahead_ratio: float,
    generated_order_ttl_bars: int,
) -> None:
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash 必须是正有限数")
    if not 0 < max_volume_participation <= 1:
        raise ValueError("max_volume_participation 必须位于 (0, 1]")
    if not 0 <= queue_ahead_ratio <= 1:
        raise ValueError("queue_ahead_ratio 必须位于 [0, 1]")
    if generated_order_ttl_bars < 1:
        raise ValueError("generated_order_ttl_bars 至少为 1")


def _match_current_bars(
    timestamp: datetime,
    current_bars: dict[str, FuturesIntradayBar],
    latest_bars: dict[str, FuturesIntradayBar],
    specs: dict[str, FuturesInstrumentSpec],
    positions: dict[str, PositionState],
    orders: dict[str, ReplayOrder],
    cash: float,
    result: FuturesReplayResult,
    participation: float,
    queue_ratio: float,
) -> float:
    used_volume: dict[str, int] = {}
    for order in sorted(
        orders.values(),
        key=lambda item: (
            item.request.offset != OrderOffset.CLOSE,
            item.request.submitted_at,
            item.request.order_id,
        ),
    ):
        if order.status in TERMINAL_ORDER_STATUSES:
            continue
        request = order.request
        if request.submitted_at >= timestamp:
            continue
        bar = current_bars.get(request.instrument_id.upper())
        if bar is None:
            continue
        dependency = (
            orders.get(request.depends_on_order_id)
            if request.depends_on_order_id
            else None
        )
        if dependency is not None and dependency.status != OrderStatus.FILLED:
            if dependency.status in {
                OrderStatus.CANCELLED,
                OrderStatus.EXPIRED,
                OrderStatus.REJECTED,
            }:
                finish_order(
                    order,
                    OrderStatus.CANCELLED,
                    timestamp,
                    f"依赖委托 {dependency.request.order_id} 未完全成交",
                )
            continue

        order.status = (
            OrderStatus.PARTIALLY_FILLED
            if order.filled_qty
            else OrderStatus.WORKING
        )
        order.eligible_bars_seen += 1
        cash = match_order(
            order,
            bar,
            specs,
            positions,
            latest_bars,
            cash,
            result,
            used_volume,
            participation,
            queue_ratio,
        )
        if (
            order.status not in TERMINAL_ORDER_STATUSES
            and order.eligible_bars_seen >= request.ttl_bars
        ):
            finish_order(
                order,
                OrderStatus.EXPIRED,
                timestamp,
                f"经过 {request.ttl_bars} 根可交易分钟线后仍未完全成交",
            )
    return cash


def _index_specs(
    instrument_specs: Iterable[FuturesInstrumentSpec],
) -> dict[str, FuturesInstrumentSpec]:
    items = tuple(instrument_specs)
    specs = {item.instrument_id.upper(): item for item in items}
    if not specs:
        raise ValueError("至少需要一个具体期货合约规格")
    if len(specs) != len(items):
        raise ValueError("具体期货合约规格 instrument_id 不能重复")
    cards = {card.product: card for card in load_product_cards()}
    for instrument_id, spec in specs.items():
        card = cards.get(spec.product.upper())
        if card is None:
            raise ValueError(f"实际合约 {instrument_id} 缺少品种卡")
        if (
            spec.exchange_id.upper(),
            spec.multiplier,
            spec.tick_size,
        ) != (card.exchange, card.multiplier, card.tick_size):
            raise ValueError(f"实际合约 {instrument_id} 与品种卡静态规格不一致")
    return specs


def _index_bars(
    bars: Iterable[FuturesIntradayBar],
    specs: dict[str, FuturesInstrumentSpec],
) -> dict[datetime, dict[str, FuturesIntradayBar]]:
    indexed: dict[datetime, dict[str, FuturesIntradayBar]] = {}
    seen: set[tuple[datetime, str]] = set()
    for bar in sorted(bars, key=lambda item: (item.timestamp, item.instrument_id)):
        instrument_id = bar.instrument_id.upper()
        if instrument_id not in specs:
            raise ValueError(f"分钟线缺少合约规格：{instrument_id}")
        key = (bar.timestamp, instrument_id)
        if key in seen:
            raise ValueError(f"分钟线重复：{bar.timestamp}/{instrument_id}")
        seen.add(key)
        indexed.setdefault(bar.timestamp, {})[instrument_id] = bar
    if not indexed:
        raise ValueError("至少需要一根具体合约分钟线")
    return dict(sorted(indexed.items()))


def _single_trading_day(
    timestamp: datetime,
    current_bars: dict[str, FuturesIntradayBar],
) -> date:
    trading_days = {bar.trading_day for bar in current_bars.values()}
    if len(trading_days) != 1:
        raise ValueError(f"同一 timestamp 不能跨越多个交易日：{timestamp}")
    return next(iter(trading_days))


def _first_timestamp_by_day(
    bars: dict[datetime, dict[str, FuturesIntradayBar]],
) -> dict[date, datetime]:
    result: dict[date, datetime] = {}
    for timestamp, current_bars in bars.items():
        for bar in current_bars.values():
            result.setdefault(bar.trading_day, timestamp)
    return result


def _last_timestamp_by_day(
    bars: dict[datetime, dict[str, FuturesIntradayBar]],
) -> dict[date, datetime]:
    result: dict[date, datetime] = {}
    for timestamp, current_bars in bars.items():
        for bar in current_bars.values():
            result[bar.trading_day] = timestamp
    return result


def _settle_day(
    trading_day: date,
    timestamp: datetime,
    latest_bars: dict[str, FuturesIntradayBar],
    specs: dict[str, FuturesInstrumentSpec],
    positions: dict[str, PositionState],
    cash: float,
    result: FuturesReplayResult,
    orders: dict[str, ReplayOrder],
    participation: float,
) -> float:
    for instrument_id, position in positions.items():
        bar = latest_bars.get(instrument_id)
        if bar is None or bar.trading_day != trading_day:
            raise ValueError(f"持仓合约缺少当日分钟线：{trading_day}/{instrument_id}")
        cash += (
            position.qty
            * (bar.settlement - position.settlement_price)
            * specs[instrument_id].multiplier
        )
        position.settlement_price = bar.settlement

    used_volume: dict[str, int] = {}
    margin = margin_required(trading_day, latest_bars, specs, positions)
    for instrument_id, position in sorted(
        list(positions.items()),
        key=lambda item: abs(item[1].qty)
        * latest_bars[item[0]].settlement
        * specs[item[0]].multiplier
        * latest_bars[item[0]].margin_rate,
        reverse=True,
    ):
        if margin <= cash + 1e-8:
            break
        bar = latest_bars[instrument_id]
        order_id = f"margin-{trading_day.isoformat()}-{instrument_id.lower()}"
        request = ReplayOrderRequest(
            order_id=order_id,
            submitted_at=timestamp - timedelta(microseconds=1),
            instrument_id=instrument_id,
            side=OrderSide.SELL if position.qty > 0 else OrderSide.BUY,
            offset=OrderOffset.CLOSE,
            qty=abs(position.qty),
            reason="margin_call",
            ttl_bars=1,
        )
        order = ReplayOrder(request=request, status=OrderStatus.WORKING)
        orders[order_id] = order
        cash = match_order(
            order,
            bar,
            specs,
            positions,
            latest_bars,
            cash,
            result,
            used_volume,
            participation,
            0.0,
        )
        if order.status != OrderStatus.FILLED:
            finish_order(
                order,
                OrderStatus.REJECTED,
                timestamp,
                "保证金追缴平仓未能完全成交",
            )
            result.rejected_orders.append(
                f"{timestamp.isoformat()}/{order_id}: 保证金追缴平仓未能完全成交"
            )
        margin = margin_required(trading_day, latest_bars, specs, positions)
    if margin > cash + 1e-8:
        raise ValueError(
            f"{trading_day} 日终保证金不足且无法可靠完成强平，回放已停止"
        )
    return cash
