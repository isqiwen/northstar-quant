"""AKShare 实际合约日线下载、缓存与画像编排。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import math
from pathlib import Path
from time import sleep
from typing import Any

import polars as pl

from northstar_quant.data.contracts.product_cards import ProductCard, load_product_cards
from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.config.trading_profile import TradingProfile
from northstar_quant.data.market.futures_actual import is_actual_futures_daily_profile
from northstar_quant.data.sources.providers.akshare_actual.builder import (
    assemble_actual_daily_dataset,
    required_rule_dates,
)
from northstar_quant.data.sources.providers.akshare_actual.normalization import (
    fetch_exchange_daily,
    fetch_jin10_rules,
    standardize_actual_daily_market,
    standardize_jin10_rule_snapshot,
)
from northstar_quant.data.artifacts.storage import save_parquet


def download_akshare_actual_daily(profile: TradingProfile) -> pl.DataFrame:
    """下载实际合约日线，并用前一交易日规则构造无未来函数的回测数据集。"""

    _require_actual_daily_profile(profile)
    products = _product_exchanges(profile)
    position_limits = _research_position_limits(profile, products)
    start = _configured_date(profile.data.download.start_date, "start_date", required=True)
    end = _configured_date(profile.data.download.end_date, "end_date", required=False)
    if start > end:
        raise ValueError("data.download.start_date 不能晚于 end_date")
    interval = _request_interval_seconds(profile.data.download.options)

    cards = {card.product: card for card in load_product_cards()}
    _validate_product_cards(products, cards)
    downloads_dir = get_settings().downloads_dir / "akshare_actual_daily"
    bars = _download_market_bars(
        products,
        start=start - timedelta(days=14),
        end=end,
        request_interval_seconds=interval,
        cache_dir=downloads_dir / "market_bars",
    )
    rules = _download_reference_rules(
        products,
        rule_dates=required_rule_dates(
            bars,
            products=set(products),
            start=start,
            end=end,
        ),
        request_interval_seconds=interval,
        cache_dir=downloads_dir / "reference_rules",
    )
    return assemble_actual_daily_dataset(
        bars,
        rules,
        products=products,
        cards=cards,
        position_limits=position_limits,
        start=start,
        end=end,
    )


def _download_market_bars(
    products: dict[str, str],
    *,
    start: date,
    end: date,
    request_interval_seconds: float,
    cache_dir: Path,
) -> pl.DataFrame:
    by_exchange: dict[str, list[str]] = defaultdict(list)
    for product, exchange in products.items():
        by_exchange[exchange].append(product)

    frames: list[pl.DataFrame] = []
    requested = False
    for exchange, exchange_products in sorted(by_exchange.items()):
        product_key = "-".join(sorted(exchange_products))
        cache_path = (
            cache_dir
            / exchange
            / f"{product_key}__{start:%Y%m%d}-{end:%Y%m%d}.parquet"
        )
        if cache_path.is_file():
            frames.append(
                _load_cached_market_bars(
                    cache_path,
                    exchange=exchange,
                    products=set(exchange_products),
                    start=start,
                    end=end,
                )
            )
            continue
        if requested:
            sleep(request_interval_seconds)
        standardized = standardize_actual_daily_market(
            fetch_exchange_daily(exchange, start, end),
            exchange=exchange,
            products=set(exchange_products),
        )
        requested = True
        save_parquet(standardized, cache_path)
        frames.append(standardized)
    return pl.concat(frames, how="vertical").sort(["date", "product", "symbol"])


def _download_reference_rules(
    products: dict[str, str],
    *,
    rule_dates: list[date],
    request_interval_seconds: float,
    cache_dir: Path,
) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    issues: list[str] = []
    requested = False
    for rule_date in rule_dates:
        cache_path = cache_dir / f"{rule_date:%Y%m%d}.parquet"
        if cache_path.is_file():
            frames.append(_load_cached_rule_snapshot(cache_path, rule_date, set(products)))
            continue
        if requested:
            sleep(request_interval_seconds)
        try:
            snapshot = standardize_jin10_rule_snapshot(
                fetch_jin10_rules(rule_date),
                selection_date=rule_date,
                products=set(products),
            )
        except (RuntimeError, ValueError) as exc:
            issues.append(f"{rule_date}: {exc}")
            requested = True
            continue
        requested = True
        save_parquet(snapshot, cache_path)
        frames.append(snapshot)
    if issues:
        raise ValueError(
            "实际合约参考规则存在缺口，成功日期已缓存；"
            + "；".join(issues)
        )
    if not frames:
        raise ValueError("实际合约数据没有可用于主力选择的前序交易日")
    return pl.concat(frames, how="vertical").sort(["selection_date", "product"])


def _load_cached_rule_snapshot(
    path: Path,
    rule_date: date,
    products: set[str],
) -> pl.DataFrame:
    snapshot = pl.read_parquet(path)
    expected_columns = {
        "selection_date",
        "product",
        "active_contract",
        "margin_rate",
        "commission_open_per_lot",
        "commission_open_rate",
        "commission_close_per_lot",
        "commission_close_rate",
        "commission_close_today_per_lot",
        "commission_close_today_rate",
        "upper_limit_rate",
        "lower_limit_rate",
    }
    missing_columns = sorted(expected_columns.difference(snapshot.columns))
    if missing_columns:
        raise ValueError(
            f"规则缓存 {path} 缺少字段：{', '.join(missing_columns)}；请删除该文件后重试"
        )
    filtered = snapshot.filter(
        (pl.col("selection_date") == rule_date)
        & pl.col("product").is_in(sorted(products))
    )
    actual_products = set(filtered.get_column("product").to_list())
    if actual_products != products:
        missing_products = sorted(products.difference(actual_products))
        raise ValueError(
            f"规则缓存 {path} 缺少品种：{', '.join(missing_products)}；请删除该文件后重试"
        )
    return filtered.sort("product")


def _load_cached_market_bars(
    path: Path,
    *,
    exchange: str,
    products: set[str],
    start: date,
    end: date,
) -> pl.DataFrame:
    frame = pl.read_parquet(path)
    required_columns = {
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
    }
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        raise ValueError(
            f"行情缓存 {path} 缺少字段：{', '.join(missing_columns)}；请删除该文件后重试"
        )
    actual_products = set(frame.get_column("product").unique().to_list())
    actual_exchanges = set(frame.get_column("exchange").unique().to_list())
    actual_start = frame.get_column("date").min()
    actual_end = frame.get_column("date").max()
    if (
        actual_products != products
        or actual_exchanges != {exchange}
        or not isinstance(actual_start, date)
        or not isinstance(actual_end, date)
        or actual_start < start
        or actual_end > end
    ):
        raise ValueError(f"行情缓存 {path} 与请求范围不一致；请删除该文件后重试")
    return frame.sort(["date", "product", "symbol"])


def _require_actual_daily_profile(profile: TradingProfile) -> None:
    if not is_actual_futures_daily_profile(profile):
        raise ValueError("akshare_actual_daily 仅支持 futures_daily 实际合约日线画像")
    if profile.futures is None or profile.futures.symbols_are_continuous:
        raise ValueError("akshare_actual_daily 要求 futures.symbols_are_continuous=false")


def _product_exchanges(profile: TradingProfile) -> dict[str, str]:
    symbols = tuple(symbol.strip().upper() for symbol in profile.data.download.symbols)
    if not symbols:
        raise ValueError("实际合约日线下载必须在 data.download.symbols 中列出品种代码")
    if len(symbols) != len(set(symbols)):
        raise ValueError("data.download.symbols 中的品种代码不能重复")
    raw = profile.data.download.options.get("product_exchanges")
    if not isinstance(raw, dict):
        raise ValueError("data.download.options.product_exchanges 必须是品种到交易所的映射")
    normalized = {
        str(product).strip().upper(): str(exchange).strip().upper()
        for product, exchange in raw.items()
    }
    if set(normalized) != set(symbols) or not all(normalized.values()):
        raise ValueError("product_exchanges 必须且只能覆盖 data.download.symbols")
    return {product: normalized[product] for product in symbols}


def _research_position_limits(
    profile: TradingProfile,
    products: dict[str, str],
) -> dict[str, int]:
    raw = profile.data.download.options.get("research_position_limit_lots")
    if not isinstance(raw, dict):
        raise ValueError(
            "data.download.options.research_position_limit_lots "
            "必须是品种到研究限仓手数的映射"
        )
    result: dict[str, int] = {}
    for product, value in raw.items():
        normalized = str(product).strip().upper()
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{normalized} 的研究限仓手数必须是正整数")
        result[normalized] = value
    if set(result) != set(products):
        raise ValueError("research_position_limit_lots 必须且只能覆盖 data.download.symbols")
    return result


def _validate_product_cards(
    products: dict[str, str],
    cards: dict[str, ProductCard],
) -> None:
    for product, exchange in products.items():
        card = cards.get(product)
        if card is None:
            raise ValueError(f"实际合约下载品种 {product} 缺少品种卡")
        if card.exchange != exchange:
            raise ValueError(f"品种 {product} 的交易所配置与品种卡不一致")


def _configured_date(value: str | None, field_name: str, *, required: bool) -> date:
    if value is None:
        if required:
            raise ValueError(f"data.download.{field_name} 为实际合约日线下载必填项")
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"data.download.{field_name} 必须使用 YYYY-MM-DD") from exc


def _request_interval_seconds(options: dict[str, Any]) -> float:
    value = options.get("request_interval_seconds", 0.2)
    if isinstance(value, bool):
        raise ValueError("request_interval_seconds 必须是非负数")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("request_interval_seconds 必须是非负数") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError("request_interval_seconds 必须是非负有限数")
    return result
