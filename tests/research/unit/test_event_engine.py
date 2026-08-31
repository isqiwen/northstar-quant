from datetime import date, timedelta

import pandas as pd
import polars as pl
import pytest

from northstar_quant.research.backtest.event_engine import (
    _drawdown_from_initial_equity,
    run_event_backtest,
)


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


def test_event_backtest_terminal_flatten_charges_close_costs_and_turnover():
    market_df, targets = _single_symbol_case()
    initial_cash = 100_000.0
    kwargs = {
        "initial_cash": initial_cash,
        "commission_bps": 0.01,
        "min_commission": 10.0,
        "slippage_bps": 30.0,
    }

    open_result = run_event_backtest(market_df, targets, **kwargs)
    flattened = run_event_backtest(market_df, targets, terminal_flatten=True, **kwargs)

    open_final_equity = float(open_result.equity_curve[-1]["equity"]) * initial_cash
    expected_close_cost = max(open_final_equity * 0.01 / 10_000.0, 10.0) + (
        open_final_equity * 30.0 / 10_000.0
    )

    assert float(flattened.equity_curve[-1]["equity"]) == pytest.approx(
        (open_final_equity - expected_close_cost) / initial_cash
    )
    assert flattened.total_return == pytest.approx(
        float(flattened.equity_curve[-1]["equity"]) - 1.0
    )
    assert float(flattened.turnover_curve[-1]["turnover"]) == pytest.approx(1.0)
    assert flattened.turnover_estimate == pytest.approx(
        open_result.turnover_estimate + 1.0 / len(open_result.turnover_curve)
    )
    assert flattened.max_drawdown == pytest.approx(
        min(float(row["drawdown"]) for row in flattened.drawdown_curve)
    )


def test_event_backtest_terminal_flatten_defaults_to_false():
    market_df, targets = _single_symbol_case()
    kwargs = {
        "initial_cash": 100_000.0,
        "commission_bps": 20.0,
        "min_commission": 10.0,
        "slippage_bps": 30.0,
    }

    default_result = run_event_backtest(market_df, targets, **kwargs)
    explicit_false_result = run_event_backtest(
        market_df,
        targets,
        terminal_flatten=False,
        **kwargs,
    )

    assert explicit_false_result == default_result


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


def test_event_backtest_honors_explicit_zero_weight_as_flatten_signal():
    start = date(2024, 1, 2)
    dates = [start + timedelta(days=offset) for offset in range(6)]
    market_df = pl.DataFrame(
        {
            "date": dates,
            "symbol": ["RB_CONT"] * len(dates),
            "close": [100.0, 110.0, 120.0, 110.0, 55.0, 60.0],
        }
    )
    targets = pl.DataFrame(
        {
            "date": [dates[2], dates[3]],
            "symbol": ["RB_CONT", "RB_CONT"],
            "target_weight": [1.0, 0.0],
        }
    )

    result = run_event_backtest(market_df, targets, execution_delay_sessions=1)

    equities = {row["date"]: row["equity"] for row in result.equity_curve}
    assert equities[dates[4].isoformat()] == pytest.approx(equities[dates[3].isoformat()])


def test_drawdown_includes_initial_equity_before_first_observation():
    equity = pd.Series([0.9, 0.95, 1.1, 1.0])

    drawdown = _drawdown_from_initial_equity(equity)

    assert drawdown.tolist() == pytest.approx([-0.1, -0.05, 0.0, -1.0 / 11.0])


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
