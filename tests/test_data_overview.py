from datetime import date, datetime

import polars as pl

from northstar_quant.data.overview import (
    build_data_overview_metrics,
    build_data_snapshot_table,
    build_normalized_price_frame,
    build_recent_candles,
    build_symbol_summary_table,
)


def test_build_symbol_summary_table_for_daily_data():
    market_df = pl.DataFrame(
        [
            {
                "date": date(2024, 1, 2),
                "symbol": "SPY",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "adjusted_close": 100.5,
                "volume": 1000,
                "dividend": 0.0,
                "split_factor": 1.0,
            },
            {
                "date": date(2024, 1, 3),
                "symbol": "SPY",
                "open": 101.0,
                "high": 103.0,
                "low": 100.0,
                "close": 102.0,
                "adjusted_close": 101.5,
                "volume": 1200,
                "dividend": 0.2,
                "split_factor": 1.0,
            },
            {
                "date": date(2024, 1, 2),
                "symbol": "QQQ",
                "open": 200.0,
                "high": 205.0,
                "low": 199.0,
                "close": 204.0,
                "adjusted_close": 203.0,
                "volume": 1500,
                "dividend": 0.0,
                "split_factor": 1.0,
            },
        ]
    )

    summary_df = build_symbol_summary_table(market_df)

    assert summary_df["symbol"].tolist() == ["QQQ", "SPY"]
    spy = summary_df.loc[summary_df["symbol"] == "SPY"].iloc[0]
    assert spy["rows"] == 2
    assert str(spy["start"]) == "2024-01-02"
    assert str(spy["end"]) == "2024-01-03"
    assert spy["latest_close"] == 102.0
    assert spy["latest_adjusted_close"] == 101.5
    assert spy["total_volume"] == 2200
    assert spy["total_dividend"] == 0.2
    assert spy["cumulative_split_factor"] == 1.0


def test_build_normalized_price_frame_keeps_first_and_last_points():
    market_df = pl.DataFrame(
        [
            {"date": date(2024, 1, 2), "symbol": "SPY", "adjusted_close": 100.0},
            {"date": date(2024, 1, 3), "symbol": "SPY", "adjusted_close": 105.0},
            {"date": date(2024, 1, 4), "symbol": "SPY", "adjusted_close": 110.0},
            {"date": date(2024, 1, 5), "symbol": "SPY", "adjusted_close": 120.0},
        ]
    )

    normalized_df = build_normalized_price_frame(
        market_df,
        price_column="adjusted_close",
        symbols=["SPY"],
        max_points_per_symbol=2,
    )

    assert normalized_df["symbol"].tolist() == ["SPY", "SPY", "SPY"]
    assert normalized_df["normalized_price"].tolist()[0] == 1.0
    assert normalized_df["normalized_price"].tolist()[-1] == 1.2
    assert str(normalized_df["time"].tolist()[-1]) == "2024-01-05"


def test_build_recent_candles_uses_timestamp_for_intraday_data():
    market_df = pl.DataFrame(
        [
            {
                "date": date(2024, 1, 2),
                "timestamp": datetime(2024, 1, 2, 9, 30),
                "symbol": "AAPL",
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 5000,
            },
            {
                "date": date(2024, 1, 2),
                "timestamp": datetime(2024, 1, 2, 9, 31),
                "symbol": "AAPL",
                "open": 100.5,
                "high": 101.5,
                "low": 100.0,
                "close": 101.0,
                "volume": 6000,
            },
        ]
    )

    candle_df = build_recent_candles(market_df, symbol="AAPL", limit=10)

    assert list(candle_df.columns) == ["time", "symbol", "open", "high", "low", "close", "volume"]
    assert candle_df["time"].iloc[-1] == datetime(2024, 1, 2, 9, 31)
    assert candle_df["close"].iloc[-1] == 101.0


def test_build_data_snapshot_and_metrics():
    market_df = pl.DataFrame(
        [
            {
                "date": date(2024, 1, 2),
                "symbol": "SPY",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "adjusted_close": 100.3,
                "volume": 1000,
                "dividend": 0.0,
                "split_factor": 1.0,
            },
            {
                "date": date(2024, 1, 3),
                "symbol": "SPY",
                "open": 100.5,
                "high": 101.5,
                "low": 100.0,
                "close": 101.2,
                "adjusted_close": 101.0,
                "volume": 1100,
                "dividend": 0.0,
                "split_factor": 1.0,
            },
        ]
    )
    manifest = {
        "data_source": "yfinance",
        "currency": "USD",
        "price_field": "adjusted_close",
        "row_count": 2,
        "symbol_count": 1,
        "start": "2024-01-02",
        "end": "2024-01-03",
        "market": "US",
        "asset_type": "ETF",
        "data_frequency": "1d",
        "rebalance_frequency": "1d",
    }

    snapshot_df = build_data_snapshot_table(market_df, limit=1)
    metrics = build_data_overview_metrics(manifest)

    assert str(snapshot_df["date"].iloc[0]) == "2024-01-03"
    assert metrics["data_source"] == "yfinance"
    assert metrics["currency"] == "USD"
    assert metrics["price_field"] == "adjusted_close"
    assert metrics["data_frequency"] == "1d"
