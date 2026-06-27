from pathlib import Path

from northstar_quant.config.settings import get_settings
from northstar_quant.data.downloader import download_profile_data, read_profile_manifest


def test_download_profile_data_writes_dataset_cache_and_manifest(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    downloads_dir = tmp_path / "downloads"

    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("NORTHSTAR_DOWNLOADS_DIR", str(downloads_dir))
    get_settings.cache_clear()

    try:
        result = download_profile_data("cn_stock_daily", provider_override="demo")
        dataset_path = Path(result.dataset_path)
        cache_path = Path(result.cache_path)

        assert dataset_path.exists()
        assert cache_path.exists()
        assert dataset_path == storage_dir / "market" / Path("cn/equity/1d/core.parquet")
        assert cache_path == downloads_dir / Path("demo/cn/equity/1d/core.parquet")

        manifest = read_profile_manifest("cn_stock_daily")
        assert manifest["profile_id"] == "cn_stock_daily"
        assert manifest["dimensions"]["market"] == "CN"
        assert manifest["dimensions"]["asset_type"] == "EQUITY"
        assert manifest["dimensions"]["data_frequency"] == "1d"
        assert manifest["dimensions"]["rebalance_frequency"] == "1d"
        assert manifest["dimensions"]["strategy_family"] == "cross_sectional_selection"
        assert manifest["dimension_key"] == "cn::equity::1d::1d::cross_sectional_selection"
        assert manifest["data_source"] == "demo"
        assert manifest["currency"] == "CNY"
        assert manifest["asset_type"] == "EQUITY"
        assert manifest["data_frequency"] == "1d"
        assert manifest["rebalance_frequency"] == "1d"
        assert manifest["strategy_family"] == "cross_sectional_selection"
        assert manifest["price_field"] == "adjusted_close"
        assert manifest["schema"]["schema_version"] == "market_data_v2"
        assert manifest["symbol_count"] == 10
        assert "600519.SS" in manifest["symbols"]
        assert manifest["columns"] == [
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
    finally:
        get_settings.cache_clear()


def test_download_intraday_profile_contains_timestamp_column(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    downloads_dir = tmp_path / "downloads"

    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("NORTHSTAR_DOWNLOADS_DIR", str(downloads_dir))
    get_settings.cache_clear()

    try:
        result = download_profile_data("cn_stock_intraday_1m", provider_override="demo")
        manifest = read_profile_manifest("cn_stock_intraday_1m")

        assert Path(result.dataset_path).exists()
        assert manifest["data_frequency"] == "1m"
        assert manifest["rebalance_frequency"] == "5m"
        assert manifest["strategy_family"] == "intraday_breakout"
        assert manifest["price_field"] == "close"
        assert manifest["currency"] == "CNY"
        assert "timestamp" in manifest["columns"]
        assert manifest["symbol_count"] == 5
        assert manifest["start"] is not None
        assert manifest["end"] is not None
        assert "11:30" not in manifest["end"]
    finally:
        get_settings.cache_clear()
