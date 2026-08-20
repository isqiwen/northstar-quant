from datetime import date, timedelta

import polars as pl
import pytest

from northstar_quant.portfolio_risk.allocation.allocator import normalize_weights
from northstar_quant.research.strategies.futures_trend import FuturesTrendStrategy


def test_futures_trend_generates_long_short_targets_and_preserves_gross_exposure():
    rows: list[dict] = []
    for offset in range(4):
        current_date = date(2024, 1, 2) + timedelta(days=offset)
        rows.extend(
            [
                {"date": current_date, "symbol": "RB_CONT", "close": 100.0 + offset},
                {"date": current_date, "symbol": "I_CONT", "close": 100.0 - offset},
            ]
        )

    targets = FuturesTrendStrategy(lookback_days=2).generate_targets(pl.DataFrame(rows))
    latest = targets.filter(pl.col("date") == targets["date"].max()).sort("symbol")
    normalized = normalize_weights(latest)

    assert latest["target_weight"].to_list() == pytest.approx([-0.5, 0.5])
    assert float(normalized["target_weight"].abs().sum()) == pytest.approx(1.0)


def test_futures_trend_emits_explicit_zero_target_when_signal_returns_to_flat():
    start = date(2024, 1, 2)
    market = pl.DataFrame(
        {
            "date": [start + timedelta(days=offset) for offset in range(4)],
            "symbol": ["RB_CONT"] * 4,
            "close": [100.0, 110.0, 120.0, 110.0],
        }
    )

    targets = FuturesTrendStrategy(lookback_days=2).generate_targets(market)

    assert targets.select("target_weight").to_series().to_list() == pytest.approx(
        [1.0, 0.0]
    )
    assert targets.filter(pl.col("target_weight") == 0.0).height == 1
