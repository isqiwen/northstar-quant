"""实际期货分钟订单生命周期和撮合测试。"""

from dataclasses import replace
from datetime import timedelta

import polars as pl

from northstar_quant.backtest.futures_daily import FuturesInstrumentSpec
from northstar_quant.backtest.futures_intraday import (
    FuturesIntradayBar,
    OrderOffset,
    OrderSide,
    OrderStatus,
    OrderType,
    ReplayCancellation,
    ReplayOrderRequest,
    run_intraday_futures_replay,
)
from tests.support.futures_actual import actual_futures_intraday_frame


def _bars() -> list[FuturesIntradayBar]:
    frame = actual_futures_intraday_frame(day_count=2, roll_offset=1).filter(
        pl.col("symbol") == "RB2405"
    )
    result: list[FuturesIntradayBar] = []
    for row in frame.to_dicts():
        result.append(
            FuturesIntradayBar(
                trading_day=row["date"],
                timestamp=row["timestamp"],
                instrument_id=row["symbol"],
                session=row["session"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                open_interest=float(row["open_interest"]),
                bid_price=float(row["bid_price"]),
                ask_price=float(row["ask_price"]),
                bid_volume=float(row["bid_volume"]),
                ask_volume=float(row["ask_volume"]),
                settlement=float(row["settlement"]),
                pre_settlement=float(row["pre_settlement"]),
                upper_limit=float(row["upper_limit"]),
                lower_limit=float(row["lower_limit"]),
                margin_rate=float(row["margin_rate"]),
                commission_open_per_lot=float(row["commission_open_per_lot"]),
                commission_open_rate=float(row["commission_open_rate"]),
                commission_close_per_lot=float(row["commission_close_per_lot"]),
                commission_close_rate=float(row["commission_close_rate"]),
                commission_close_today_per_lot=float(
                    row["commission_close_today_per_lot"]
                ),
                commission_close_today_rate=float(
                    row["commission_close_today_rate"]
                ),
                max_position_lots=int(row["max_position_lots"]),
                is_trading_day_end=bool(row["is_trading_day_end"]),
                session_complete=bool(row["session_complete"]),
            )
        )
    return result


def _spec() -> FuturesInstrumentSpec:
    return FuturesInstrumentSpec(
        instrument_id="RB2405",
        product="RB",
        exchange_id="SHFE",
        multiplier=10,
        tick_size=1,
    )


def test_market_order_waits_for_next_bar_and_partially_fills():
    bars = _bars()
    limited = [
        replace(bar, ask_volume=2.0, volume=2.0)
        for bar in bars
    ]
    order = ReplayOrderRequest(
        order_id="market-open",
        submitted_at=limited[0].timestamp,
        instrument_id="RB2405",
        side=OrderSide.BUY,
        offset=OrderOffset.OPEN,
        qty=3,
        ttl_bars=2,
    )

    result = run_intraday_futures_replay(
        bars=limited,
        instrument_specs=[_spec()],
        orders=[order],
        max_volume_participation=1.0,
    )

    state = next(item for item in result.orders if item.request.order_id == order.order_id)
    assert state.status == OrderStatus.FILLED
    assert state.filled_qty == 3
    assert [trade.timestamp for trade in result.trades[:2]] == [
        limited[1].timestamp,
        limited[2].timestamp,
    ]


def test_passive_limit_order_consumes_queue_then_can_be_cancelled():
    bars = _bars()
    order = ReplayOrderRequest(
        order_id="passive-open",
        submitted_at=bars[0].timestamp,
        instrument_id="RB2405",
        side=OrderSide.BUY,
        offset=OrderOffset.OPEN,
        qty=10,
        order_type=OrderType.LIMIT,
        limit_price=bars[1].low,
        ttl_bars=5,
    )
    cancellation = ReplayCancellation(
        order_id=order.order_id,
        requested_at=bars[2].timestamp,
    )

    result = run_intraday_futures_replay(
        bars=bars,
        instrument_specs=[_spec()],
        orders=[order],
        cancellations=[cancellation],
        max_volume_participation=0.001,
        queue_ahead_ratio=1.0,
    )

    state = next(item for item in result.orders if item.request.order_id == order.order_id)
    assert state.status == OrderStatus.CANCELLED
    assert state.filled_qty == 0
    assert result.trades == []


def test_partially_filled_order_preserves_fill_when_cancelled():
    bars = [replace(bar, ask_volume=1.0, volume=1.0) for bar in _bars()]
    order = ReplayOrderRequest(
        order_id="partial-then-cancel",
        submitted_at=bars[0].timestamp,
        instrument_id="RB2405",
        side=OrderSide.BUY,
        offset=OrderOffset.OPEN,
        qty=3,
        ttl_bars=5,
    )

    result = run_intraday_futures_replay(
        bars=bars,
        instrument_specs=[_spec()],
        orders=[order],
        cancellations=[
            ReplayCancellation(
                order_id=order.order_id,
                requested_at=bars[2].timestamp,
            )
        ],
        max_volume_participation=1.0,
    )

    state = next(item for item in result.orders if item.request.order_id == order.order_id)
    assert state.status == OrderStatus.CANCELLED
    assert state.filled_qty == 1
    assert len(result.trades) == 1


def test_unfilled_order_expires_after_configured_bars():
    bars = _bars()
    order = ReplayOrderRequest(
        order_id="unmarketable-limit",
        submitted_at=bars[0].timestamp - timedelta(minutes=1),
        instrument_id="RB2405",
        side=OrderSide.BUY,
        offset=OrderOffset.OPEN,
        qty=1,
        order_type=OrderType.LIMIT,
        limit_price=bars[0].lower_limit,
        ttl_bars=2,
    )

    result = run_intraday_futures_replay(
        bars=bars,
        instrument_specs=[_spec()],
        orders=[order],
    )

    state = next(item for item in result.orders if item.request.order_id == order.order_id)
    assert state.status == OrderStatus.EXPIRED
    assert state.eligible_bars_seen == 2
