from __future__ import annotations

from datetime import date

import pandas as pd

from northstar_quant.common.enums import (
    AssetType,
    DataFrequency,
    Market,
    RebalanceFrequency,
    StrategyFamily,
)
from northstar_quant.config.trading_profile import (
    ProfileDataConfig,
    ProfileDownloadConfig,
    TradingProfile,
)
from northstar_quant.data.yfinance_provider import download_yfinance_dataset


class _FakeYFinance:
    @staticmethod
    def download(*, tickers, start, end, interval, auto_adjust, progress, actions, threads, timeout):
        assert tickers in {"SPY", "QQQ"}
        assert start == "2020-01-01"
        assert end == "2020-01-11"
        assert interval == "1d"
        assert auto_adjust is False
        assert progress is False
        assert actions is True
        assert threads is False
        assert timeout == 15.0
        idx = pd.to_datetime(["2020-01-02", "2020-01-03"])
        return pd.DataFrame(
            {
                ("Open", tickers): [100.0, 101.0],
                ("High", tickers): [101.0, 102.0],
                ("Low", tickers): [99.0, 100.0],
                ("Close", tickers): [100.5, 101.5],
                ("Adj Close", tickers): [99.5, 100.8],
                ("Dividends", tickers): [0.0, 0.2],
                ("Stock Splits", tickers): [0.0, 0.0],
                ("Volume", tickers): [1_000_000, 1_100_000],
            },
            index=idx,
        )


def test_download_yfinance_dataset_normalizes_daily_ohlcv(monkeypatch):
    profile = TradingProfile(
        profile_id="test_yfinance_daily",
        name="test",
        market=Market.US,
        asset_type=AssetType.ETF,
        data_frequency=DataFrequency.D1,
        rebalance_frequency=RebalanceFrequency.D1,
        strategy_family=StrategyFamily.MOMENTUM_ROTATION,
        currency="USD",
        timezone="America/New_York",
        calendar="XNYS",
        universe_id="test",
        benchmark_symbol="SPY",
        data=ProfileDataConfig(
            provider="local",
            dataset_id="test",
            path="us/etf/1d/test.parquet",
            adjusted=True,
            download=ProfileDownloadConfig(
                enabled=True,
                provider="yfinance",
                symbols=("SPY", "QQQ"),
                start_date="2020-01-01",
                end_date="2020-01-10",
                options={"auto_adjust": True, "timeout_seconds": 15},
            ),
        ),
    )

    monkeypatch.setattr(
        "northstar_quant.data.yfinance_provider._import_yfinance",
        lambda: _FakeYFinance(),
    )

    df = download_yfinance_dataset(profile)

    assert df.columns == [
        "date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
        "dividend",
        "split_factor",
    ]
    assert df.height == 4
    assert set(df["symbol"].to_list()) == {"SPY", "QQQ"}
    assert df["date"].min() == date(2020, 1, 2)
    assert df["adjusted_close"].min() == 99.5
    assert df["dividend"].sum() == 0.4
