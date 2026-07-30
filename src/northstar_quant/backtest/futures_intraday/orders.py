"""分钟回放委托索引、依赖与目标权重转单。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
import math

from northstar_quant.backtest.futures_daily.models import (
    FuturesInstrumentSpec,
    PositionState,
)
from northstar_quant.backtest.futures_intraday.matching import (
    TERMINAL_ORDER_STATUSES,
    account_equity,
    finish_order,
)
from northstar_quant.backtest.futures_intraday.models import (
    FuturesIntradayBar,
    IntradayWeightTarget,
    OrderOffset,
    OrderSide,
    OrderStatus,
    ReplayCancellation,
    ReplayOrder,
    ReplayOrderRequest,
)


def index_orders(
    orders: Iterable[ReplayOrderRequest],
    specs: dict[str, FuturesInstrumentSpec],
) -> dict[str, ReplayOrder]:
    """索引并验证外部历史委托及依赖图。"""

    result: dict[str, ReplayOrder] = {}
    requests = tuple(orders)
    for request in requests:
        if request.instrument_id.upper() not in specs:
            raise ValueError(f"委托缺少合约规格：{request.instrument_id}")
        if request.order_id in result:
            raise ValueError(f"委托 order_id 重复：{request.order_id}")
        result[request.order_id] = ReplayOrder(request=request)
    for request in requests:
        if (
            request.depends_on_order_id is not None
            and request.depends_on_order_id not in result
        ):
            raise ValueError(
                f"委托 {request.order_id} 依赖不存在的委托 "
                f"{request.depends_on_order_id}"
            )
    _validate_dependency_graph(result)
    return result


def index_cancellations(
    cancellations: Iterable[ReplayCancellation],
    orders: dict[str, ReplayOrder],
) -> dict[datetime, list[ReplayCancellation]]:
    """索引撤单，并拒绝缺失委托或倒置时间。"""

    result: dict[datetime, list[ReplayCancellation]] = {}
    for cancellation in cancellations:
        order = orders.get(cancellation.order_id)
        if order is None:
            raise ValueError(f"撤单引用不存在的 order_id：{cancellation.order_id}")
        if cancellation.requested_at <= order.request.submitted_at:
            raise ValueError(f"撤单 {cancellation.order_id} 必须晚于委托提交时间")
        result.setdefault(cancellation.requested_at, []).append(cancellation)
    return result


def _validate_dependency_graph(orders: dict[str, ReplayOrder]) -> None:
    for order_id in orders:
        visited: set[str] = set()
        current: str | None = order_id
        while current is not None:
            if current in visited:
                raise ValueError(f"委托依赖存在环：{order_id}")
            visited.add(current)
            current = orders[current].request.depends_on_order_id


def schedule_weight_targets(
    targets: Iterable[IntradayWeightTarget],
    bars: dict[datetime, dict[str, FuturesIntradayBar]],
    specs: dict[str, FuturesInstrumentSpec],
) -> dict[datetime, list[IntradayWeightTarget]]:
    """把执行交易日目标安排到实际合约首根分钟线。"""

    first_by_day_instrument: dict[tuple[date, str], datetime] = {}
    for timestamp, current_bars in bars.items():
        for instrument_id, bar in current_bars.items():
            first_by_day_instrument.setdefault(
                (bar.trading_day, instrument_id),
                timestamp,
            )
    result: dict[datetime, list[IntradayWeightTarget]] = {}
    seen: set[tuple[date, str]] = set()
    for target in targets:
        instrument_id = target.instrument_id.upper()
        if instrument_id not in specs:
            raise ValueError(f"权重目标缺少合约规格：{instrument_id}")
        key = (target.execution_day, specs[instrument_id].product)
        if key in seen:
            raise ValueError(
                f"权重目标在 execution_day/product 上重复：{key[0]}/{key[1]}"
            )
        seen.add(key)
        execution_timestamp = first_by_day_instrument.get(
            (target.execution_day, instrument_id)
        )
        if execution_timestamp is None:
            raise ValueError(
                f"权重目标执行日缺少分钟线：{target.execution_day}/{instrument_id}"
            )
        result.setdefault(execution_timestamp, []).append(target)
    return result


def build_target_orders(
    timestamp: datetime,
    targets: Iterable[IntradayWeightTarget],
    current_bars: dict[str, FuturesIntradayBar],
    latest_bars: dict[str, FuturesIntradayBar],
    specs: dict[str, FuturesInstrumentSpec],
    positions: dict[str, PositionState],
    cash: float,
    ttl_bars: int,
    *,
    existing_order_ids: set[str],
) -> list[ReplayOrderRequest]:
    """将保证金权重目标转换为先平后开的依赖委托。"""

    generated: list[ReplayOrderRequest] = []
    submitted_at = timestamp - timedelta(microseconds=1)
    equity = account_equity(latest_bars, specs, positions, cash)
    for target in targets:
        instrument_id = target.instrument_id.upper()
        spec = specs[instrument_id]
        bar = current_bars[instrument_id]
        per_lot_margin = bar.ask_price * spec.multiplier * bar.margin_rate
        desired_abs = min(
            math.floor(abs(equity * target.target_weight) / per_lot_margin),
            bar.max_position_lots,
        )
        desired_qty = desired_abs if target.target_weight >= 0 else -desired_abs
        product_positions = [
            (key, value)
            for key, value in sorted(positions.items())
            if specs[key].product == spec.product and value.qty != 0
        ]
        dependency: str | None = None
        stale_position_seen = False
        current_qty = positions.get(
            instrument_id,
            PositionState(0, bar.pre_settlement, None, None),
        ).qty

        for position_id, position in product_positions:
            needs_full_close = (
                position_id != instrument_id
                or desired_qty == 0
                or position.qty * desired_qty < 0
            )
            if not needs_full_close:
                continue
            stale_position_seen = stale_position_seen or position_id != instrument_id
            order_id = _generated_order_id(
                target,
                position_id,
                "close",
                existing_order_ids,
            )
            generated.append(
                ReplayOrderRequest(
                    order_id=order_id,
                    submitted_at=submitted_at,
                    instrument_id=position_id,
                    side=OrderSide.SELL if position.qty > 0 else OrderSide.BUY,
                    offset=OrderOffset.CLOSE,
                    qty=abs(position.qty),
                    reason=(
                        "roll_close"
                        if position_id != instrument_id
                        else "target_reverse_close"
                    ),
                    ttl_bars=ttl_bars,
                    depends_on_order_id=dependency,
                )
            )
            existing_order_ids.add(order_id)
            dependency = order_id
            if position_id == instrument_id:
                current_qty = 0

        if desired_qty == 0:
            continue
        same_direction = current_qty * desired_qty > 0
        delta = desired_qty - current_qty if same_direction else desired_qty
        if same_direction and abs(desired_qty) < abs(current_qty):
            order_id = _generated_order_id(
                target,
                instrument_id,
                "reduce",
                existing_order_ids,
            )
            generated.append(
                ReplayOrderRequest(
                    order_id=order_id,
                    submitted_at=submitted_at,
                    instrument_id=instrument_id,
                    side=OrderSide.SELL if current_qty > 0 else OrderSide.BUY,
                    offset=OrderOffset.CLOSE,
                    qty=abs(delta),
                    reason="target_reduce",
                    ttl_bars=ttl_bars,
                    depends_on_order_id=dependency,
                )
            )
            existing_order_ids.add(order_id)
            continue
        if delta == 0:
            continue
        order_id = _generated_order_id(
            target,
            instrument_id,
            "open",
            existing_order_ids,
        )
        generated.append(
            ReplayOrderRequest(
                order_id=order_id,
                submitted_at=submitted_at,
                instrument_id=instrument_id,
                side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                offset=OrderOffset.OPEN,
                qty=abs(delta),
                reason="roll_open" if stale_position_seen else "target_open",
                ttl_bars=ttl_bars,
                depends_on_order_id=dependency,
            )
        )
        existing_order_ids.add(order_id)
    return generated


def _generated_order_id(
    target: IntradayWeightTarget,
    instrument_id: str,
    action: str,
    existing: set[str],
) -> str:
    base = (
        f"target-{target.execution_day.isoformat()}-"
        f"{instrument_id.lower()}-{action}"
    )
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def apply_cancellations(
    timestamp: datetime,
    cancellations: Iterable[ReplayCancellation],
    orders: dict[str, ReplayOrder],
) -> None:
    """在同 timestamp 撮合前应用撤单。"""

    for cancellation in cancellations:
        order = orders[cancellation.order_id]
        if order.status not in TERMINAL_ORDER_STATUSES:
            finish_order(
                order,
                OrderStatus.CANCELLED,
                timestamp,
                "收到历史撤单请求",
            )
