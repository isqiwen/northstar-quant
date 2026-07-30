from datetime import date, timedelta

import polars as pl
import pytest

from northstar_quant.backtest.event_engine import run_event_backtest


def test_run_event_backtest_supports_month_end_resample():
    start = date(2024, 1, 1)
    market_rows: list[dict] = []
    target_rows: list[dict] = []

    price_a = 100.0
    price_b = 100.0

    for offset in range(45):
        current = start + timedelta(days=offset)
        if current.weekday() >= 5:
            continue

        price_a += 0.6
        price_b += 0.2
        market_rows.extend(
            [
                {"date": current, "symbol": "AAA", "close": price_a},
                {"date": current, "symbol": "BBB", "close": price_b},
            ]
        )
        target_rows.extend(
            [
                {"date": current, "symbol": "AAA", "target_weight": 0.5},
                {"date": current, "symbol": "BBB", "target_weight": 0.5},
            ]
        )

    market_df = pl.DataFrame(market_rows)
    targets = pl.DataFrame(target_rows).with_columns(signal_value=pl.lit(1.0))

    result = run_event_backtest(market_df, targets)

    assert result.total_return > 0
    assert result.annualized_return > 0
    assert len(result.equity_curve) > 0
    assert len(result.monthly_returns) >= 1
    assert len(result.turnover_curve) == len(result.equity_curve)


def _single_symbol_case() -> tuple[pl.DataFrame, pl.DataFrame]:
    dates = [date(2024, 1, 2) + timedelta(days=offset) for offset in range(6)]
    market_df = pl.DataFrame(
        {
            "date": dates,
            "symbol": ["RB_CONT"] * len(dates),
            "close": [100.0, 101.0, 103.0, 106.0, 110.0, 115.0],
        }
    )
    targets = pl.DataFrame(
        {
            "date": dates,
            "symbol": ["RB_CONT"] * len(dates),
            "target_weight": [1.0] * len(dates),
        }
    )
    return market_df, targets


def test_event_backtest_applies_costs_and_minimum_commission_to_equity():
    market_df, targets = _single_symbol_case()

    free = run_event_backtest(market_df, targets, initial_cash=100_000.0)
    costly = run_event_backtest(
        market_df,
        targets,
        initial_cash=100_000.0,
        commission_bps=20.0,
        min_commission=10.0,
        slippage_bps=30.0,
    )
    low_capital = run_event_backtest(
        market_df,
        targets,
        initial_cash=1_000.0,
        min_commission=10.0,
    )
    high_capital = run_event_backtest(
        market_df,
        targets,
        initial_cash=100_000.0,
        min_commission=10.0,
    )

    assert costly.total_return < free.total_return
    assert low_capital.total_return < high_capital.total_return


def test_event_backtest_execution_delay_changes_realized_return():
    market_df, targets = _single_symbol_case()

    one_session = run_event_backtest(
        market_df,
        targets,
        execution_delay_sessions=1,
    )
    three_sessions = run_event_backtest(
        market_df,
        targets,
        execution_delay_sessions=3,
    )

    assert one_session.total_return > three_sessions.total_return


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"lot_size": 10}, "不支持手数取整"),
        ({"sellable_after_sessions": 1}, "不支持 T\\+N"),
    ],
)
def test_event_backtest_rejects_assumptions_it_cannot_model(kwargs, message):
    market_df, targets = _single_symbol_case()

    with pytest.raises(ValueError, match=message):
        run_event_backtest(market_df, targets, **kwargs)
