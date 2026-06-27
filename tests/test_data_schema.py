from datetime import date

import polars as pl

from northstar_quant.common.enums import (
    AssetType,
    DataFrequency,
    Market,
    RebalanceFrequency,
    StrategyFamily,
)
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import (
    ProfileDataConfig,
    ProfileDownloadConfig,
    TradingProfile,
)
from northstar_quant.data.downloader import download_profile_data, validate_profile_data
from northstar_quant.data.schema import to_signal_market_data


def test_to_signal_market_data_uses_adjusted_close_for_daily_profiles():
    profile = TradingProfile(
        profile_id="test_daily",
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
            price_field="adjusted_close",
            adjusted=True,
            download=ProfileDownloadConfig(provider="local"),
        ),
    )
    market_df = pl.DataFrame(
        [
            {
                "date": date(2024, 1, 2),
                "symbol": "SPY",
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 105.0,
                "adjusted_close": 84.0,
                "volume": 1_000_000,
                "dividend": 0.0,
                "split_factor": 1.0,
            }
        ]
    )

    signal_df = to_signal_market_data(profile, market_df)

    assert signal_df["close"].to_list() == [84.0]
    assert signal_df["open"].to_list() == [80.0]
    assert signal_df["high"].to_list() == [88.0]
    assert signal_df["low"].to_list() == [72.0]


def test_validate_profile_data_reports_standard_daily_schema(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    downloads_dir = tmp_path / "downloads"

    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("NORTHSTAR_DOWNLOADS_DIR", str(downloads_dir))
    get_settings.cache_clear()

    try:
        download_profile_data("cn_stock_daily", provider_override="demo")
        report = validate_profile_data("cn_stock_daily")

        assert report["status"] == "ok"
        assert report["schema_version"] == "market_data_v2"
        assert report["data_source"] == "demo"
        assert report["currency"] == "CNY"
        assert report["configured_price_field"] == "adjusted_close"
        assert report["expected_columns"] == [
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
        assert report["duplicate_key_count"] == 0
    finally:
        get_settings.cache_clear()
