"""统一市场数据 schema、质量校验和信号价格口径转换。

日线标准列为 ``date, symbol, open, high, low, close, adjusted_close, volume``。期货连续
合约研究通常使用 ``close``；若选择 ``adjusted_close``，本模块会以调整因子同步变换
OHLC，避免只替换 close 而造成高低价与收盘价口径不一致。
"""

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
    """返回频率默认价格列。

    日线和周线默认偏向 ``adjusted_close``，以适应存在复权语义的资产；期货画像应在
    YAML 中显式设为 ``close``，避免把主力连续拼接数据误认为股票复权价。
    """

    if data_frequency in {DataFrequency.D1, DataFrequency.W1}:
        return "adjusted_close"
    return "close"


def expected_market_columns(profile: TradingProfile | str | None = None) -> list[str]:
    """按画像频率返回必须存在的标准字段，返回副本避免调用方修改全局常量。"""

    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    if profile_obj.data_frequency in {DataFrequency.D1, DataFrequency.W1}:
        return list(STANDARD_DAILY_COLUMNS)
    return list(STANDARD_INTRADAY_COLUMNS)


def validate_market_dataset(
    profile: TradingProfile | str | None,
    df: pl.DataFrame,
) -> dict[str, Any]:
    """校验数据字段、主键唯一性、空值与画像价格口径。

    日线主键是 ``(date, symbol)``，日内主键是 ``(timestamp, symbol)``。任一必需字段
    缺失、存在空值、主键重复或配置价格列不存在都会直接失败；下载器不会悄悄补值，
    防止错误数据进入指标和回测。
    """

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
    """将原始标准行情转换为策略应读取的统一价格口径。

    若使用 ``adjusted_close``，调整因子为 ``f = adjusted_close / close``，输出
    ``open'=open×f, high'=high×f, low'=low×f, close'=adjusted_close``。当原 close 接近
    零时 ``f`` 取 1，避免除零。配置列缺失时保持原表，由前置 schema 校验负责拒绝。
    """

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
