from datetime import date, timedelta

import polars as pl

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
