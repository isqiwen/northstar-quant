"""实际期货合约日线的数据契约与连续信号序列构造。

原始数据按具体合约保存，并重复携带每个品种逐日生效的主力合约。主力选择日必须早于
生效交易日，因此回测可以在不使用未来成交量或持仓量的前提下生成显式换月指令。
策略读取的 ``*_CONT`` 序列由实际主力合约的单日收益链接而成，不包含跨合约价格跳跃。
"""

from __future__ import annotations

from datetime import date
import math
from typing import Any

import polars as pl

from northstar_quant.platform.common.enums import AssetType, DataFrequency
from northstar_quant.data_platform.contracts.instrument_universes import (
    load_instrument_universe,
    validate_actual_product_membership,
)
from northstar_quant.data_platform.contracts.product_cards import load_product_cards
from northstar_quant.platform.config.trading_profile import TradingProfile

ACTUAL_FUTURES_DAILY_SCHEMA_VERSION = "actual_futures_daily_v1"
ACTUAL_FUTURES_DAILY_COLUMNS = [
    "date",
    "symbol",
    "product",
    "exchange",
    "open",
    "high",
    "low",
    "close",
    "settlement",
    "pre_settlement",
    "volume",
    "open_interest",
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
    "first_session",
    "session_complete",
]

_PRICE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "settlement",
    "pre_settlement",
    "upper_limit",
    "lower_limit",
)
_NON_NEGATIVE_COLUMNS = (
    "volume",
    "open_interest",
    "commission_open_per_lot",
    "commission_open_rate",
    "commission_close_per_lot",
    "commission_close_rate",
    "commission_close_today_per_lot",
    "commission_close_today_rate",
)


def is_actual_futures_daily_profile(profile: TradingProfile) -> bool:
    """判断画像是否使用实际合约逐日撮合引擎。"""

    return (
        profile.asset_type == AssetType.FUTURES
        and profile.data_frequency == DataFrequency.D1
        and profile.backtest.engine == "futures_daily"
    )


def is_actual_futures_profile(profile: TradingProfile) -> bool:
    """判断画像是否使用任一种实际合约撮合引擎。"""

    return profile.asset_type == AssetType.FUTURES and profile.backtest.engine in {
        "futures_daily",
        "futures_intraday_replay",
    }


def validate_actual_futures_dataset(
    profile: TradingProfile,
    df: pl.DataFrame,
) -> dict[str, Any]:
    """严格校验实际合约链、动态交易规则和无未来函数主力日历。"""

    if df.is_empty():
        raise ValueError(f"画像 {profile.profile_id} 的实际合约数据集不能为空")
    missing = [column for column in ACTUAL_FUTURES_DAILY_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(
            f"画像 {profile.profile_id} 的实际合约数据集缺少字段: "
            + ", ".join(missing)
        )
    if df.schema["date"] != pl.Date or df.schema["selection_date"] != pl.Date:
        raise ValueError("实际合约 date 和 selection_date 必须是 Parquet/CSV 日期类型")
    if df.schema["session_complete"] != pl.Boolean:
        raise ValueError("实际合约 session_complete 必须是布尔类型")

    for column in ACTUAL_FUTURES_DAILY_COLUMNS:
        null_count = int(df.get_column(column).null_count())
        if null_count:
            raise ValueError(
                f"画像 {profile.profile_id} 的实际合约字段 {column} "
                f"存在 {null_count} 个空值"
            )

    duplicate_count = (
        df.group_by(["date", "symbol"]).len().filter(pl.col("len") > 1).height
    )
    if duplicate_count:
        raise ValueError("实际合约数据集在主键 date, symbol 上存在重复记录")

    for column in (*_PRICE_COLUMNS, *_NON_NEGATIVE_COLUMNS, "margin_rate", "max_position_lots"):
        invalid_count = sum(
            1
            for value in df.get_column(column).to_list()
            if isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        )
        if invalid_count:
            raise ValueError(f"实际合约字段 {column} 存在非有限或非数值值")

    for column in _PRICE_COLUMNS:
        if df.filter(pl.col(column) <= 0).height:
            raise ValueError(f"实际合约价格字段 {column} 必须全部大于 0")
    for column in _NON_NEGATIVE_COLUMNS:
        if df.filter(pl.col(column) < 0).height:
            raise ValueError(f"实际合约字段 {column} 不能为负数")
    if df.filter((pl.col("margin_rate") <= 0) | (pl.col("margin_rate") > 1)).height:
        raise ValueError("实际合约 margin_rate 必须位于 (0, 1]")
    if df.filter(
        (pl.col("max_position_lots") <= 0)
        | (pl.col("max_position_lots") != pl.col("max_position_lots").floor())
    ).height:
        raise ValueError("实际合约 max_position_lots 必须是正整数")

    invalid_ohlc = df.filter(
        (pl.col("high") < pl.max_horizontal("open", "close", "low"))
        | (pl.col("low") > pl.min_horizontal("open", "close", "high"))
    ).height
    if invalid_ohlc:
        raise ValueError("实际合约数据集存在不一致 OHLC")
    if df.filter(
        (pl.col("lower_limit") >= pl.col("upper_limit"))
        | (pl.col("low") < pl.col("lower_limit"))
        | (pl.col("high") > pl.col("upper_limit"))
        | (pl.col("settlement") < pl.col("low"))
        | (pl.col("settlement") > pl.col("high"))
    ).height:
        raise ValueError("实际合约 OHLC/settlement 必须位于当日价格范围内")
    commission_rate_columns = (
        "commission_open_rate",
        "commission_close_rate",
        "commission_close_today_rate",
    )
    if any(df.filter(pl.col(column) > 1).height for column in commission_rate_columns):
        raise ValueError("实际合约手续费率必须使用 [0, 1] 内的小数")

    normalized = df.with_columns(
        pl.col("symbol").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("product").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("exchange").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("active_contract").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("first_session").cast(pl.String).str.strip_chars().str.to_lowercase(),
    )
    for column in ("symbol", "product", "exchange", "active_contract"):
        if normalized.filter(pl.col(column) == "").height:
            raise ValueError(f"实际合约字段 {column} 不能为空")
    if normalized.filter(
        pl.col("symbol").str.ends_with("_CONT")
        | pl.col("active_contract").str.ends_with("_CONT")
    ).height:
        raise ValueError("实际合约数据不得把连续研究代码作为可交易合约")
    if normalized.filter(~pl.col("first_session").is_in(["night", "day"])).height:
        raise ValueError("first_session 仅允许 night 或 day")
    if normalized.filter(pl.col("session_complete") != True).height:  # noqa: E712
        raise ValueError("存在未完整覆盖夜盘/日盘的数据，已拒绝回测")
    if normalized.filter(pl.col("selection_date") >= pl.col("date")).height:
        raise ValueError("主力合约 selection_date 必须早于生效交易日，禁止未来函数")

    cards = {card.product: card for card in load_product_cards()}
    dataset_products = set(normalized.get_column("product").unique().to_list())
    unknown_products = sorted(dataset_products.difference(cards))
    if unknown_products:
        raise ValueError("实际合约数据包含未建品种卡的品种：" + ", ".join(unknown_products))
    for product in sorted(dataset_products):
        card = cards[product]
        product_rows = normalized.filter(pl.col("product") == product)
        exchanges = set(product_rows.get_column("exchange").unique().to_list())
        if exchanges != {card.exchange}:
            raise ValueError(f"品种 {product} 的 exchange 与品种卡不一致")
    symbol_identity_count = (
        normalized.select("symbol", "product", "exchange")
        .unique()
        .group_by("symbol")
        .len()
        .filter(pl.col("len") != 1)
        .height
    )
    if symbol_identity_count:
        raise ValueError("同一实际合约 symbol 的 product/exchange 必须保持一致")

    _validate_active_contract_schedule(normalized)
    _validate_pre_settlement_continuity(normalized)
    actual_symbols = sorted(normalized.get_column("symbol").unique().to_list())
    observed_product_exchanges = {
        product: str(
            normalized.filter(pl.col("product") == product)
            .get_column("exchange")
            .unique()
            .item()
        )
        for product in dataset_products
    }
    universe_validation = validate_actual_product_membership(
        load_instrument_universe(profile.universe_id),
        observed_product_exchanges,
    )
    return {
        "schema_version": ACTUAL_FUTURES_DAILY_SCHEMA_VERSION,
        "expected_columns": list(ACTUAL_FUTURES_DAILY_COLUMNS),
        "primary_key": ["date", "symbol"],
        "currency": profile.currency,
        "timezone": profile.timezone,
        "calendar": profile.calendar,
        "configured_price_field": profile.data.price_field,
        "null_counts": {column: 0 for column in ACTUAL_FUTURES_DAILY_COLUMNS},
        "duplicate_key_count": 0,
        "finite_numeric_values": True,
        "positive_price_values": True,
        "ohlc_consistent": True,
        "actual_symbols": actual_symbols,
        "products": sorted(dataset_products),
        "universe": universe_validation,
        "no_lookahead_active_contracts": True,
        "complete_trading_sessions": True,
    }


def build_adjusted_continuous_signal_data(df: pl.DataFrame) -> pl.DataFrame:
    """把显式主力合约链转换为无换月跳跃的连续信号指数。

    第一个交易日的指数从 1 开始。之后每个交易日使用当日主力合约相对其上一交易日
    收盘价的收益链接指数；发生换月时不会拿新合约价格除以前一日旧合约价格。
    """

    normalized = df.with_columns(
        pl.col("symbol").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("product").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("active_contract").cast(pl.String).str.strip_chars().str.to_uppercase(),
    )
    records = normalized.sort(["date", "product", "symbol"]).to_dicts()
    by_key = {
        (record["date"], str(record["symbol"])): record
        for record in records
    }
    active_by_day: dict[tuple[date, str], str] = {}
    for record in records:
        key = (record["date"], str(record["product"]))
        active = str(record["active_contract"])
        existing = active_by_day.setdefault(key, active)
        if existing != active:
            raise ValueError(f"{key[0]}/{key[1]} 存在多个 active_contract")

    output: list[dict[str, object]] = []
    products = sorted({key[1] for key in active_by_day})
    for product in products:
        days = sorted(key[0] for key in active_by_day if key[1] == product)
        previous_day: date | None = None
        previous_index = 1.0
        for current_day in days:
            active = active_by_day[(current_day, product)]
            current = by_key.get((current_day, active))
            if current is None:
                raise ValueError(f"{current_day}/{product} 的主力合约 {active} 缺少行情")
            if previous_day is None:
                open_index = high_index = low_index = close_index = 1.0
            else:
                previous_contract = by_key.get((previous_day, active))
                if previous_contract is None:
                    raise ValueError(
                        f"{current_day}/{product} 的主力合约 {active} "
                        f"缺少上一交易日行情，无法构造无跳跃信号"
                    )
                denominator = float(previous_contract["close"])
                open_index = previous_index * float(current["open"]) / denominator
                high_index = previous_index * float(current["high"]) / denominator
                low_index = previous_index * float(current["low"]) / denominator
                close_index = previous_index * float(current["close"]) / denominator
            output.append(
                {
                    "date": current_day,
                    "symbol": f"{product}_CONT",
                    "open": open_index,
                    "high": high_index,
                    "low": low_index,
                    "close": close_index,
                    "adjusted_close": close_index,
                    "volume": float(current["volume"]),
                    "source_contract": active,
                }
            )
            previous_day = current_day
            previous_index = close_index
    return pl.DataFrame(output).sort(["date", "symbol"])


def active_contract_rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    """返回去重后的逐日主力合约表，供换月和目标映射使用。"""

    normalized = df.with_columns(
        pl.col("product").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("active_contract").cast(pl.String).str.strip_chars().str.to_uppercase(),
    )
    return (
        normalized.select("date", "product", "active_contract", "selection_date")
        .unique()
        .sort(["date", "product"])
        .to_dicts()
    )


def _validate_active_contract_schedule(df: pl.DataFrame) -> None:
    schedules = (
        df.select("date", "product", "active_contract", "selection_date")
        .unique()
        .group_by(["date", "product"])
        .len()
    )
    if schedules.filter(pl.col("len") != 1).height:
        raise ValueError("每个 date/product 必须且只能有一个主力合约与选择日期")

    available = {
        (row["date"], str(row["product"]), str(row["symbol"]))
        for row in df.select("date", "product", "symbol").iter_rows(named=True)
    }
    for row in active_contract_rows(df):
        key = (row["date"], str(row["product"]), str(row["active_contract"]))
        if key not in available:
            raise ValueError(
                f"{row['date']}/{row['product']} 的主力合约 "
                f"{row['active_contract']} 不在当日合约链中"
            )


def _validate_pre_settlement_continuity(df: pl.DataFrame) -> None:
    for symbol in df.get_column("symbol").unique().to_list():
        rows = (
            df.filter(pl.col("symbol") == symbol)
            .sort("date")
            .select("date", "settlement", "pre_settlement")
            .to_dicts()
        )
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
