from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from northstar_quant.backtest.daily_stateful import (
    DailyBacktestConfig,
    DailyOrderStatus,
    run_daily_stateful_backtest,
)


def test_target_decision_fills_next_session_and_is_cash_limited():
    result = run_daily_stateful_backtest(
        _market(
            [
                ("AAA", 10.0, 10.0),
                ("AAA", 11.0, 12.0),
            ]
        ),
        _targets([(0, "AAA", 1.0)]),
        config=DailyBacktestConfig(initial_cash=1_000.0),
    )

    assert len(result.orders) == 1
    order = result.orders[0]
    assert order.decision_date == _day(0)
    assert order.scheduled_date == _day(1)
    assert order.status == DailyOrderStatus.PARTIALLY_FILLED
    assert order.filled_qty == 90
    assert order.unfilled_qty == 10
    assert order.reason == "cash_limited"
    assert result.fills[0].trade_date == _day(1)
    assert result.fills[0].execution_price == 11.0
    assert result.portfolio_snapshots[0].equity == 1_000.0
    assert result.portfolio_snapshots[1].equity == 1_090.0
    assert result.summary.total_return == pytest.approx(0.09)


def test_sell_orders_execute_before_buys_and_missing_target_means_exit():
    result = run_daily_stateful_backtest(
        _market(
            [
                ("AAA", 10.0, 10.0),
                ("BBB", 10.0, 10.0),
                ("AAA", 10.0, 10.0),
                ("BBB", 10.0, 10.0),
                ("AAA", 10.0, 10.0),
                ("BBB", 10.0, 10.0),
            ]
        ),
        _targets([(0, "AAA", 1.0), (1, "BBB", 1.0)]),
        config=DailyBacktestConfig(initial_cash=1_000.0),
    )

    assert [(fill.trade_date, fill.symbol, fill.side, fill.qty) for fill in result.fills] == [
        (_day(1), "AAA", "BUY", 100),
        (_day(2), "AAA", "SELL", 100),
        (_day(2), "BBB", "BUY", 100),
    ]
    final_positions = [
        snapshot
        for snapshot in result.position_snapshots
        if snapshot.date == _day(2)
    ]
    assert [(snapshot.symbol, snapshot.qty) for snapshot in final_positions] == [("BBB", 100)]
    assert result.portfolio_snapshots[-1].cash == 0.0


def test_unsellable_position_rejects_exit_instead_of_silently_shorting():
    result = run_daily_stateful_backtest(
        _market(
            [
                ("AAA", 10.0, 10.0),
                ("AAA", 10.0, 10.0),
                ("AAA", 10.0, 10.0),
            ]
        ),
        _targets([(0, "AAA", 1.0), (1, "AAA", 0.0)]),
        config=DailyBacktestConfig(
            initial_cash=1_000.0,
            sellable_after_sessions=2,
        ),
    )

    exit_order = result.orders[-1]
    assert exit_order.side == "SELL"
    assert exit_order.status == DailyOrderStatus.REJECTED
    assert exit_order.reason == "insufficient_sellable_qty"
    assert result.position_snapshots[-1].qty == 100


def test_commission_slippage_and_lot_size_are_applied_to_buy_fill():
    result = run_daily_stateful_backtest(
        _market([("AAA", 10.0, 10.0), ("AAA", 10.0, 10.0)]),
        _targets([(0, "AAA", 1.0)]),
        config=DailyBacktestConfig(
            initial_cash=1_000.0,
            commission_bps=10.0,
            min_commission=1.0,
            slippage_bps=100.0,
            lot_size=10,
        ),
    )

    fill = result.fills[0]
    assert fill.execution_price == pytest.approx(10.1)
    assert fill.qty == 90
    assert fill.commission == pytest.approx(1.0)
    assert result.portfolio_snapshots[-1].cash == pytest.approx(90.0)


def test_order_without_future_session_expires_and_invalid_targets_fail_closed():
    result = run_daily_stateful_backtest(
        _market([("AAA", 10.0, 10.0)]),
        _targets([(0, "AAA", 1.0)]),
    )

    assert result.orders[0].status == DailyOrderStatus.EXPIRED
    assert result.orders[0].reason == "no_future_session"
    assert not result.fills

    with pytest.raises(ValueError, match="目标权重总和不能超过 1"):
        run_daily_stateful_backtest(
            _market([("AAA", 10.0, 10.0), ("BBB", 10.0, 10.0)]),
            _targets([(0, "AAA", 0.8), (0, "BBB", 0.8)]),
        )

    with pytest.raises(ValueError, match="lot_size 必须是大于 0 的整数"):
        DailyBacktestConfig(lot_size=1.5)  # type: ignore[arg-type]


def _market(rows: list[tuple[str, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "date": _day(index // len({item[0] for item in rows})),
                "symbol": symbol,
                "open": open_price,
                "close": close_price,
            }
            for index, (symbol, open_price, close_price) in enumerate(rows)
        ]
    )


def _targets(rows: list[tuple[int, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"date": _day(offset), "symbol": symbol, "target_weight": target_weight}
            for offset, symbol, target_weight in rows
        ]
    )


def _day(offset: int) -> date:
    return date(2024, 1, 2) + timedelta(days=offset)
