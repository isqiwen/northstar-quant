"""Market data schema, validation, and signal-price helpers."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import polars as pl

from northstar_quant.common.enums import DataFrequency
from northstar_quant.config.trading_profile import TradingProfile, load_trading_profile

SCHEMA_VERSION = "market_data_v2"
STANDARD_DAILY_COLUMNS = [
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
STANDARD_INTRADAY_COLUMNS = [
    "date",
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


def default_price_field(data_frequency: DataFrequency) -> str:
    if data_frequency in {DataFrequency.D1, DataFrequency.W1}:
        return "adjusted_close"
    return "close"


def expected_market_columns(profile: TradingProfile | str | None = None) -> list[str]:
    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    if profile_obj.data_frequency in {DataFrequency.D1, DataFrequency.W1}:
        return list(STANDARD_DAILY_COLUMNS)
    return list(STANDARD_INTRADAY_COLUMNS)


def validate_market_dataset(
    profile: TradingProfile | str | None,
    df: pl.DataFrame,
) -> dict[str, Any]:
    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    expected_columns = expected_market_columns(profile_obj)
    missing_columns = [column for column in expected_columns if column not in df.columns]
    if missing_columns:
        missing_text = ", ".join(missing_columns)
        raise ValueError(
            f"画像 {profile_obj.profile_id} 的数据集缺少标准字段: {missing_text}"
        )

    primary_key = (
        ["timestamp", "symbol"]
        if profile_obj.data_frequency not in {DataFrequency.D1, DataFrequency.W1}
        else ["date", "symbol"]
    )
    duplicate_key_count = (
        df.group_by(primary_key).len().filter(pl.col("len") > 1).height
        if df.height > 0
        else 0
    )
    if duplicate_key_count:
        key_text = ", ".join(primary_key)
        raise ValueError(
            f"画像 {profile_obj.profile_id} 的数据集在主键 {key_text} 上存在重复记录"
        )

    null_counts: dict[str, int] = {}
    for column in expected_columns:
        null_count = int(df.get_column(column).null_count())
        null_counts[column] = null_count
        if null_count:
            raise ValueError(
                f"画像 {profile_obj.profile_id} 的数据集字段 {column} 存在 {null_count} 个空值"
            )

    configured_price_field = profile_obj.data.price_field or default_price_field(
        profile_obj.data_frequency
    )
    if configured_price_field not in df.columns:
        raise ValueError(
            f"画像 {profile_obj.profile_id} 配置的 price_field={configured_price_field} 不在数据集中"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "expected_columns": expected_columns,
        "primary_key": primary_key,
        "currency": profile_obj.currency,
        "timezone": profile_obj.timezone,
        "calendar": profile_obj.calendar,
        "configured_price_field": configured_price_field,
        "default_price_field": default_price_field(profile_obj.data_frequency),
        "null_counts": null_counts,
        "duplicate_key_count": duplicate_key_count,
        "dimensions": asdict(profile_obj.dimensions),
    }


def to_signal_market_data(
    profile: TradingProfile | str | None,
    market_df: pl.DataFrame,
) -> pl.DataFrame:
    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    configured_price_field = profile_obj.data.price_field or default_price_field(
        profile_obj.data_frequency
    )

    if configured_price_field == "close" or configured_price_field not in market_df.columns:
        return market_df

    if configured_price_field == "adjusted_close" and "adjusted_close" in market_df.columns:
        adjusted_factor = (
            pl.when(pl.col("close").abs() > 1e-12)
            .then(pl.col("adjusted_close") / pl.col("close"))
            .otherwise(1.0)
            .alias("_adjustment_factor")
        )
        return (
            market_df.with_columns(adjusted_factor)
            .with_columns(
                (pl.col("open") * pl.col("_adjustment_factor")).alias("open"),
                (pl.col("high") * pl.col("_adjustment_factor")).alias("high"),
                (pl.col("low") * pl.col("_adjustment_factor")).alias("low"),
                pl.col("adjusted_close").alias("close"),
            )
            .drop("_adjustment_factor")
        )

    return market_df.with_columns(pl.col(configured_price_field).alias("close"))
