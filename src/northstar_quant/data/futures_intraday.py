"""实际期货合约分钟回放数据契约与日线信号聚合。"""

from __future__ import annotations

import math
from typing import Any

import polars as pl

from northstar_quant.common.enums import AssetType, DataFrequency
from northstar_quant.config.instrument_universes import (
    load_instrument_universe,
    validate_actual_product_membership,
)
from northstar_quant.config.product_cards import load_product_cards
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.data.futures_actual import (
    active_contract_rows,
    build_adjusted_continuous_signal_data,
)

ACTUAL_FUTURES_INTRADAY_SCHEMA_VERSION = "actual_futures_intraday_v1"
ACTUAL_FUTURES_INTRADAY_COLUMNS = [
    "date",
    "timestamp",
    "symbol",
    "product",
    "exchange",
    "session",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "bid_price",
    "ask_price",
    "bid_volume",
    "ask_volume",
    "settlement",
    "pre_settlement",
    "upper_limit",
    "lower_limit",
    "margin_rate",
    "commission_open_per_lot",
    "commission_open_rate",
    "commission_close_per_lot",
    "commission_close_rate",
    "commission_close_today_per_lot",
    "commission_close_today_rate",
    "max_position_lots",
    "active_contract",
    "selection_date",
    "is_trading_day_end",
    "session_complete",
]

_PRICE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "bid_price",
    "ask_price",
    "settlement",
    "pre_settlement",
    "upper_limit",
    "lower_limit",
)
_NON_NEGATIVE_COLUMNS = (
    "volume",
    "open_interest",
    "bid_volume",
    "ask_volume",
    "commission_open_per_lot",
    "commission_open_rate",
    "commission_close_per_lot",
    "commission_close_rate",
    "commission_close_today_per_lot",
    "commission_close_today_rate",
)
_DAILY_RULE_COLUMNS = [
    "product",
    "exchange",
    "settlement",
    "pre_settlement",
    "upper_limit",
    "lower_limit",
    "margin_rate",
    "commission_open_per_lot",
    "commission_open_rate",
    "commission_close_per_lot",
    "commission_close_rate",
    "commission_close_today_per_lot",
    "commission_close_today_rate",
    "max_position_lots",
    "active_contract",
    "selection_date",
    "session_complete",
]


def is_actual_futures_intraday_profile(profile: TradingProfile) -> bool:
    """判断画像是否使用实际合约分钟订单回放。"""

    return (
        profile.asset_type == AssetType.FUTURES
        and profile.data_frequency == DataFrequency.M1
        and profile.backtest.engine == "futures_intraday_replay"
    )


def validate_actual_futures_intraday_dataset(
    profile: TradingProfile,
    df: pl.DataFrame,
) -> dict[str, Any]:
    """严格校验分钟行情、盘口、动态规则和交易日完整性。"""

    if df.is_empty():
        raise ValueError(f"画像 {profile.profile_id} 的实际合约分钟数据集不能为空")
    missing = [
        column for column in ACTUAL_FUTURES_INTRADAY_COLUMNS if column not in df.columns
    ]
    if missing:
        raise ValueError(
            f"画像 {profile.profile_id} 的实际合约分钟数据集缺少字段: "
            + ", ".join(missing)
        )
    if df.schema["date"] != pl.Date or df.schema["selection_date"] != pl.Date:
        raise ValueError("分钟回放 date 和 selection_date 必须是日期类型")
    if not isinstance(df.schema["timestamp"], pl.Datetime):
        raise ValueError("分钟回放 timestamp 必须是 Datetime 类型")
    for column in ("is_trading_day_end", "session_complete"):
        if df.schema[column] != pl.Boolean:
            raise ValueError(f"分钟回放 {column} 必须是布尔类型")

    for column in ACTUAL_FUTURES_INTRADAY_COLUMNS:
        null_count = int(df.get_column(column).null_count())
        if null_count:
            raise ValueError(
                f"画像 {profile.profile_id} 的分钟字段 {column} "
                f"存在 {null_count} 个空值"
            )
    duplicate_count = (
        df.group_by(["timestamp", "symbol"])
        .len()
        .filter(pl.col("len") > 1)
        .height
    )
    if duplicate_count:
        raise ValueError("分钟回放数据在主键 timestamp, symbol 上存在重复记录")

    for column in (*_PRICE_COLUMNS, *_NON_NEGATIVE_COLUMNS, "margin_rate", "max_position_lots"):
        invalid_count = sum(
            1
            for value in df.get_column(column).to_list()
            if isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        )
        if invalid_count:
            raise ValueError(f"分钟回放字段 {column} 存在非有限或非数值值")
    for column in _PRICE_COLUMNS:
        if df.filter(pl.col(column) <= 0).height:
            raise ValueError(f"分钟回放价格字段 {column} 必须全部大于 0")
    for column in _NON_NEGATIVE_COLUMNS:
        if df.filter(pl.col(column) < 0).height:
            raise ValueError(f"分钟回放字段 {column} 不能为负数")
    if df.filter((pl.col("margin_rate") <= 0) | (pl.col("margin_rate") > 1)).height:
        raise ValueError("分钟回放 margin_rate 必须位于 (0, 1]")
    if df.filter(
        (pl.col("max_position_lots") <= 0)
        | (pl.col("max_position_lots") != pl.col("max_position_lots").floor())
    ).height:
        raise ValueError("分钟回放 max_position_lots 必须是正整数")

    invalid_ohlc = df.filter(
        (pl.col("high") < pl.max_horizontal("open", "close", "low"))
        | (pl.col("low") > pl.min_horizontal("open", "close", "high"))
    ).height
    if invalid_ohlc:
        raise ValueError("分钟回放数据存在不一致 OHLC")
    if df.filter(
        (pl.col("lower_limit") >= pl.col("upper_limit"))
        | (pl.col("low") < pl.col("lower_limit"))
        | (pl.col("high") > pl.col("upper_limit"))
        | (pl.col("bid_price") > pl.col("ask_price"))
        | (pl.col("bid_price") < pl.col("lower_limit"))
        | (pl.col("ask_price") > pl.col("upper_limit"))
    ).height:
        raise ValueError("分钟 OHLC/盘口必须位于涨跌停范围内，且 bid_price <= ask_price")
    if any(
        df.filter(pl.col(column) > 1).height
        for column in (
            "commission_open_rate",
            "commission_close_rate",
            "commission_close_today_rate",
        )
    ):
        raise ValueError("分钟回放手续费率必须使用 [0, 1] 内的小数")

    normalized = _normalize(df)
    for column in ("symbol", "product", "exchange", "active_contract"):
        if normalized.filter(pl.col(column) == "").height:
            raise ValueError(f"分钟回放字段 {column} 不能为空")
    if normalized.filter(
        pl.col("symbol").str.ends_with("_CONT")
        | pl.col("active_contract").str.ends_with("_CONT")
    ).height:
        raise ValueError("分钟回放不得把连续研究代码作为可成交合约")
    if normalized.filter(~pl.col("session").is_in(["night", "day"])).height:
        raise ValueError("分钟回放 session 仅允许 night 或 day")
    if normalized.filter(pl.col("session_complete") != True).height:  # noqa: E712
        raise ValueError("存在未完整覆盖夜盘/日盘的分钟数据，已拒绝回测")
    if normalized.filter(pl.col("selection_date") >= pl.col("date")).height:
        raise ValueError("主力合约 selection_date 必须早于生效交易日，禁止未来函数")

    _validate_product_cards(normalized)
    _validate_daily_rules(normalized)
    _validate_end_markers(normalized)
    _validate_timestamp_order(normalized)
    _validate_active_schedule(normalized)
    _validate_pre_settlement(normalized)
    products = set(normalized.get_column("product").unique().to_list())
    observed_product_exchanges = {
        product: str(
            normalized.filter(pl.col("product") == product)
            .get_column("exchange")
            .unique()
            .item()
        )
        for product in products
    }
    universe_validation = validate_actual_product_membership(
        load_instrument_universe(profile.universe_id),
        observed_product_exchanges,
    )
    return {
        "schema_version": ACTUAL_FUTURES_INTRADAY_SCHEMA_VERSION,
        "expected_columns": list(ACTUAL_FUTURES_INTRADAY_COLUMNS),
        "primary_key": ["timestamp", "symbol"],
        "currency": profile.currency,
        "timezone": profile.timezone,
        "calendar": profile.calendar,
        "configured_price_field": profile.data.price_field,
        "null_counts": {column: 0 for column in ACTUAL_FUTURES_INTRADAY_COLUMNS},
        "duplicate_key_count": 0,
        "finite_numeric_values": True,
        "positive_price_values": True,
        "ohlc_consistent": True,
        "products": sorted(products),
        "universe": universe_validation,
        "no_lookahead_active_contracts": True,
        "complete_trading_sessions": True,
        "quote_replay_ready": True,
    }


def build_intraday_daily_bars(df: pl.DataFrame) -> pl.DataFrame:
    """把实际合约分钟线聚合为具体合约日线，供连续信号序列构造。"""

    normalized = _normalize(df).sort(["timestamp", "symbol"])
    return (
        normalized.group_by(
            ["date", "symbol", "product", "exchange", "active_contract"],
            maintain_order=True,
        )
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
        )
        .sort(["date", "product", "symbol"])
    )


def build_intraday_continuous_signal_data(df: pl.DataFrame) -> pl.DataFrame:
    """把分钟实际合约行情转换为日频无换月跳跃连续信号。"""

    return build_adjusted_continuous_signal_data(build_intraday_daily_bars(df))


def intraday_active_contract_rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    """返回分钟数据中的逐日主力合约表。"""

    return active_contract_rows(_normalize(df))


def _normalize(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.col("symbol").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("product").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("exchange").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("active_contract").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("session").cast(pl.String).str.strip_chars().str.to_lowercase(),
    )


def _validate_product_cards(df: pl.DataFrame) -> None:
    cards = {card.product: card for card in load_product_cards()}
    products = set(df.get_column("product").unique().to_list())
    unknown = sorted(products.difference(cards))
    if unknown:
        raise ValueError("分钟回放包含未建品种卡的品种：" + ", ".join(unknown))
    for product in sorted(products):
        exchanges = set(
            df.filter(pl.col("product") == product)
            .get_column("exchange")
            .unique()
            .to_list()
        )
        if exchanges != {cards[product].exchange}:
            raise ValueError(f"品种 {product} 的 exchange 与品种卡不一致")
    identities = (
        df.select("symbol", "product", "exchange")
        .unique()
        .group_by("symbol")
        .len()
        .filter(pl.col("len") != 1)
        .height
    )
    if identities:
        raise ValueError("同一实际合约 symbol 的 product/exchange 必须保持一致")


def _validate_daily_rules(df: pl.DataFrame) -> None:
    for column in _DAILY_RULE_COLUMNS:
        inconsistent = (
            df.group_by(["date", "symbol"])
            .agg(pl.col(column).n_unique().alias("n"))
            .filter(pl.col("n") != 1)
            .height
        )
        if inconsistent:
            raise ValueError(f"分钟回放字段 {column} 在同一交易日/合约内必须保持一致")


def _validate_end_markers(df: pl.DataFrame) -> None:
    counts = (
        df.group_by(["date", "symbol"])
        .agg(pl.col("is_trading_day_end").sum().alias("ends"))
        .filter(pl.col("ends") != 1)
    )
    if counts.height:
        raise ValueError("每个交易日/合约必须且只能有一根 is_trading_day_end=true")
    not_last = (
        df.sort(["timestamp", "symbol"])
        .group_by(["date", "symbol"])
        .agg(pl.col("is_trading_day_end").last().alias("last_is_end"))
        .filter(~pl.col("last_is_end"))
        .height
    )
    if not_last:
        raise ValueError("is_trading_day_end 必须标记该交易日/合约最后一根分钟线")


def _validate_timestamp_order(df: pl.DataFrame) -> None:
    for symbol in df.get_column("symbol").unique().to_list():
        timestamps = (
            df.filter(pl.col("symbol") == symbol)
            .sort("timestamp")
            .get_column("timestamp")
            .to_list()
        )
        if timestamps != sorted(set(timestamps)):
            raise ValueError(f"合约 {symbol} 的 timestamp 必须严格递增")


def _validate_active_schedule(df: pl.DataFrame) -> None:
    schedules = (
        df.select("date", "product", "active_contract", "selection_date")
        .unique()
        .group_by(["date", "product"])
        .len()
    )
    if schedules.filter(pl.col("len") != 1).height:
        raise ValueError("每个 date/product 必须且只能有一个主力合约与选择日期")
    available = set(
        df.select("date", "product", "symbol").unique().iter_rows()
    )
    for row in intraday_active_contract_rows(df):
        key = (row["date"], row["product"], row["active_contract"])
        if key not in available:
            raise ValueError(
                f"{row['date']}/{row['product']} 的主力合约 "
                f"{row['active_contract']} 不在当日分钟合约链中"
            )


def _validate_pre_settlement(df: pl.DataFrame) -> None:
    daily = (
        df.sort(["timestamp", "symbol"])
        .group_by(["date", "symbol"], maintain_order=True)
        .agg(
            pl.col("settlement").first(),
            pl.col("pre_settlement").first(),
        )
        .sort(["symbol", "date"])
    )
    for symbol in daily.get_column("symbol").unique().to_list():
        rows = daily.filter(pl.col("symbol") == symbol).to_dicts()
        for previous, current in zip(rows, rows[1:], strict=False):
            if not math.isclose(
                float(current["pre_settlement"]),
                float(previous["settlement"]),
                rel_tol=1e-9,
                abs_tol=1e-8,
            ):
                raise ValueError(
                    f"{current['date']}/{symbol} 的 pre_settlement "
                    "与上一交易日 settlement 不一致"
                )
