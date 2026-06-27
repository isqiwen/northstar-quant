"""内置演示数据生成器。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import polars as pl

from northstar_quant.common.enums import AssetType, DataFrequency, Market
from northstar_quant.config.trading_profile import TradingProfile, load_trading_profile
from northstar_quant.data.storage import profile_market_data_path, save_parquet

_DEFAULT_SYMBOLS: dict[str, list[str]] = {
    "cn_etf_daily": [
        "510300.SS",
        "510500.SS",
        "512100.SS",
        "510050.SS",
        "588000.SS",
        "159915.SZ",
        "512880.SS",
        "512690.SS",
        "512170.SS",
        "159928.SZ",
    ],
    "cn_etf_daily_research12": [
        "510300.SS",
        "510500.SS",
        "512100.SS",
        "510050.SS",
        "588000.SS",
        "159915.SZ",
        "512880.SS",
        "512690.SS",
        "512170.SS",
        "515790.SS",
        "159928.SZ",
        "159949.SZ",
    ],
    "cn_stock_daily": [
        "600519.SS",
        "601318.SS",
        "600036.SS",
        "600276.SS",
        "601899.SS",
        "000858.SZ",
        "000333.SZ",
        "300750.SZ",
        "002594.SZ",
        "000651.SZ",
    ],
    "cn_stock_weekly": [
        "600519.SS",
        "601318.SS",
        "600036.SS",
        "600276.SS",
        "601899.SS",
        "000858.SZ",
        "000333.SZ",
        "300750.SZ",
        "002594.SZ",
        "000651.SZ",
    ],
    "cn_stock_intraday_1m": ["600519.SS", "601318.SS", "000333.SZ", "300750.SZ", "002594.SZ"],
    "us_etf_daily": ["SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "TLT", "IEF", "GLD", "VNQ"],
    "us_stock_daily": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "UNH", "COST"],
    "us_stock_weekly": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "UNH", "COST"],
    "us_stock_intraday_1m": ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"],
}


def _as_profile(profile: TradingProfile | str | None = None) -> TradingProfile:
    return profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)


def _profile_symbols(profile: TradingProfile) -> list[str]:
    if profile.data.download.symbols:
        return list(profile.data.download.symbols)
    if profile.profile_id in _DEFAULT_SYMBOLS:
        return list(_DEFAULT_SYMBOLS[profile.profile_id])
    if profile.market == Market.CN:
        return ["510300.SS", "510500.SS", "159915.SZ"]
    return ["SPY", "QQQ", "IWM"]


def _parse_session_time(raw: str) -> time:
    hour_text, minute_text = raw.split(":", maxsplit=1)
    return time(int(hour_text), int(minute_text))


def _intraday_session_offsets(options: dict) -> list[timedelta]:
    segments = options.get("session_segments")
    if segments:
        offsets: list[timedelta] = []
        for segment in segments:
            start_time = _parse_session_time(str(segment["start"]))
            minutes = int(segment["minutes"])
            for minute_index in range(minutes):
                offsets.append(
                    timedelta(
                        hours=start_time.hour,
                        minutes=start_time.minute + minute_index,
                    )
                )
        return offsets

    session_minutes = int(options.get("session_minutes", 390))
    session_start = time(9, 30)
    return [
        timedelta(hours=session_start.hour, minutes=session_start.minute + minute_index)
        for minute_index in range(session_minutes)
    ]


def _profile_seed(profile: TradingProfile) -> int:
    return sum(ord(char) for char in profile.profile_id)


def _business_days(start: date, trading_days: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < trading_days:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _generate_daily_rows(
    symbols: list[str],
    start: date,
    trading_days: int,
    *,
    seed: int,
    price_floor: float = 20.0,
    drift_range: tuple[float, float] = (-0.00015, 0.0008),
    vol_range: tuple[float, float] = (0.008, 0.02),
    volume_range: tuple[int, int] = (500_000, 12_000_000),
) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows: list[dict] = []

    for idx, symbol in enumerate(symbols):
        price = 60.0 + rng.uniform(-5, 80) + idx * 3.0
        drift = rng.uniform(*drift_range)
        vol = rng.uniform(*vol_range)

        for current in _business_days(start, trading_days):
            ret = drift + rng.normal(0, vol)
            price = max(price * (1 + ret), price_floor)
            open_price = price * (1 - rng.uniform(0, 0.004))
            close_price = price
            high_price = max(open_price, close_price) * (1 + rng.uniform(0, 0.006))
            low_price = min(open_price, close_price) * (1 - rng.uniform(0, 0.006))
            rows.append(
                {
                    "date": current,
                    "symbol": symbol,
                    "open": round(open_price, 4),
                    "high": round(high_price, 4),
                    "low": round(low_price, 4),
                    "close": round(close_price, 4),
                    "adjusted_close": round(close_price, 4),
                    "volume": int(rng.integers(*volume_range)),
                    "dividend": 0.0,
                    "split_factor": 1.0,
                }
            )
    return rows


def _generate_weekly_dataset(profile: TradingProfile) -> pl.DataFrame:
    options = profile.data.download.options
    trading_days = int(options.get("trading_days", 420))
    start = date.fromisoformat(profile.data.download.start_date or "2023-01-01")
    rows = _generate_daily_rows(
        _profile_symbols(profile),
        start,
        trading_days,
        seed=_profile_seed(profile),
        drift_range=(-0.0002, 0.0010),
        vol_range=(0.01, 0.028),
        volume_range=(800_000, 20_000_000),
    )
    daily_df = pl.DataFrame(rows).sort(["symbol", "date"])
    pdf = daily_df.to_pandas()
    pdf["date"] = pd.to_datetime(pdf["date"])

    weekly = (
        pdf.sort_values(["symbol", "date"])
        .groupby("symbol")
        .resample("W-FRI", on="date")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "adjusted_close": "last",
                "volume": "sum",
                "dividend": "sum",
                "split_factor": "prod",
            }
        )
        .reset_index()
        .dropna(subset=["open", "close"])
    )
    weekly["date"] = weekly["date"].dt.date
    return pl.from_pandas(
        weekly[
            [
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
        ]
    ).sort(["date", "symbol"])


def _generate_intraday_dataset(profile: TradingProfile) -> pl.DataFrame:
    rng = np.random.default_rng(_profile_seed(profile))
    symbols = _profile_symbols(profile)
    options = profile.data.download.options
    trading_days = int(options.get("trading_days", 5))
    start = date.fromisoformat(profile.data.download.start_date or "2024-03-04")
    session_offsets = _intraday_session_offsets(options)

    rows: list[dict] = []
    trading_dates = _business_days(start, trading_days)

    for idx, symbol in enumerate(symbols):
        price = 80.0 + idx * 25.0 + rng.uniform(-5, 15)
        drift = rng.uniform(-0.00002, 0.00008)
        vol = rng.uniform(0.0008, 0.0025)
        for current_date in trading_dates:
            session_base = datetime.combine(current_date, time())
            for minute_offset in session_offsets:
                ret = drift + rng.normal(0, vol)
                price = max(price * (1 + ret), 5.0)
                open_price = price * (1 - rng.uniform(0, 0.001))
                close_price = price
                high_price = max(open_price, close_price) * (1 + rng.uniform(0, 0.0015))
                low_price = min(open_price, close_price) * (1 - rng.uniform(0, 0.0015))
                rows.append(
                    {
                        "date": current_date,
                        "timestamp": session_base + minute_offset,
                        "symbol": symbol,
                        "open": round(open_price, 4),
                        "high": round(high_price, 4),
                        "low": round(low_price, 4),
                        "close": round(close_price, 4),
                        "volume": int(rng.integers(10_000, 120_000)),
                    }
                )
    return pl.DataFrame(rows).sort(["timestamp", "symbol"])


def build_demo_dataset(profile: TradingProfile | str | None = None) -> pl.DataFrame:
    """根据交易画像生成一份可运行的演示数据集。"""

    profile_obj = _as_profile(profile)
    frequency = profile_obj.data_frequency
    options = profile_obj.data.download.options

    if frequency == DataFrequency.M1:
        return _generate_intraday_dataset(profile_obj)
    if frequency == DataFrequency.W1:
        return _generate_weekly_dataset(profile_obj)

    trading_days = int(options.get("trading_days", 420))
    start = date.fromisoformat(profile_obj.data.download.start_date or "2023-01-01")
    is_etf = profile_obj.asset_type == AssetType.ETF

    rows = _generate_daily_rows(
        _profile_symbols(profile_obj),
        start,
        trading_days,
        seed=_profile_seed(profile_obj),
        drift_range=(-0.0001, 0.0007) if is_etf else (-0.0002, 0.0010),
        vol_range=(0.006, 0.015) if is_etf else (0.01, 0.028),
        volume_range=(1_000_000, 8_000_000) if is_etf else (800_000, 20_000_000),
    )
    return pl.DataFrame(rows).sort(["date", "symbol"])


def create_sample_dataset(profile_id: str | None = None) -> str:
    """生成一份内置演示数据并写入画像对应的数据集路径。"""

    profile = load_trading_profile(profile_id)
    df = build_demo_dataset(profile)
    path = save_parquet(df, profile_market_data_path(profile))
    return str(path)
