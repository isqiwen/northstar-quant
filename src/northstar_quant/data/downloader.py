"""数据下载与落盘管理。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from northstar_quant.config.trading_profile import TradingProfile, load_trading_profile, list_trading_profiles
from northstar_quant.data.demo_data import build_demo_dataset
from northstar_quant.data.schema import validate_market_dataset
from northstar_quant.data.storage import (
    dataset_manifest_path,
    load_json,
    load_parquet,
    profile_download_cache_path,
    profile_market_data_path,
    save_json,
    save_parquet,
)
from northstar_quant.data.yfinance_provider import download_yfinance_dataset

DataProvider = Callable[[TradingProfile], pl.DataFrame]

_PROVIDERS: dict[str, DataProvider] = {}


@dataclass(slots=True)
class DataDownloadResult:
    profile_id: str
    data_source: str
    currency: str
    price_field: str
    schema_version: str
    dataset_path: str
    dataset_manifest_path: str
    cache_path: str
    cache_manifest_path: str
    row_count: int
    symbol_count: int
    columns: list[str]
    start: str | None
    end: str | None


def register_data_provider(provider_id: str, provider: DataProvider, *, replace: bool = False) -> None:
    """注册数据提供器。"""

    if provider_id in _PROVIDERS and not replace:
        raise ValueError(f"数据提供器已注册：{provider_id}")
    _PROVIDERS[provider_id] = provider


def list_data_providers() -> list[str]:
    """列出当前可用的数据提供器。"""

    return sorted(_PROVIDERS)


def get_data_provider(provider_id: str) -> DataProvider:
    """获取某个数据提供器。"""

    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        available = ", ".join(list_data_providers()) or "无"
        raise KeyError(f"未注册的数据提供器：{provider_id}。当前可用提供器：{available}") from exc


def _local_provider(profile: TradingProfile) -> pl.DataFrame:
    dataset_path = profile_market_data_path(profile)
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"本地数据文件不存在：{dataset_path}。"
            "请先使用支持下载的 provider，或把数据放到画像配置指定的路径。"
        )
    return load_parquet(dataset_path)


def _demo_provider(profile: TradingProfile) -> pl.DataFrame:
    return build_demo_dataset(profile)


def _temporal_range(df: pl.DataFrame) -> tuple[str | None, str | None]:
    for column in ("timestamp", "date"):
        if column in df.columns and df.height > 0:
            series = df.get_column(column)
            return str(series.min()), str(series.max())
    return None, None


def _build_manifest(
    profile: TradingProfile,
    data_source: str,
    df: pl.DataFrame,
    *,
    data_path: Path,
    validation: dict[str, Any],
) -> dict[str, Any]:
    start, end = _temporal_range(df)
    symbols: list[str] = []
    if "symbol" in df.columns and df.height > 0:
        symbols = sorted({str(symbol) for symbol in df.get_column("symbol").to_list()})

    return {
        "profile_id": profile.profile_id,
        "profile_name": profile.name,
        "dimensions": asdict(profile.dimensions),
        "dimension_key": profile.dimension_key,
        "data_source": data_source,
        "currency": profile.currency,
        "timezone": profile.timezone,
        "calendar": profile.calendar,
        "market": profile.market,
        "asset_type": profile.asset_type,
        "data_frequency": profile.data_frequency,
        "rebalance_frequency": profile.rebalance_frequency,
        "strategy_family": profile.strategy_family,
        "price_field": profile.data.price_field,
        "universe_id": profile.universe_id,
        "dataset_id": profile.data.dataset_id,
        "data_path": str(data_path),
        "row_count": df.height,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "columns": list(df.columns),
        "start": start,
        "end": end,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema": validation,
        "download": {
            "enabled": profile.data.download.enabled,
            "data_source": profile.data.download.provider,
            "symbols": list(profile.data.download.symbols),
            "start_date": profile.data.download.start_date,
            "end_date": profile.data.download.end_date,
            "options": dict(profile.data.download.options),
        },
    }


def read_profile_manifest(profile_id: str | None = None) -> dict[str, Any]:
    """读取某个交易画像对应的数据 manifest。"""

    profile = load_trading_profile(profile_id)
    manifest_path = dataset_manifest_path(profile_market_data_path(profile))
    return load_json(manifest_path)


def validate_profile_data(profile_id: str | None = None) -> dict[str, Any]:
    """Validate a profile dataset against the standardized schema."""

    profile = load_trading_profile(profile_id)
    dataset_path = profile_market_data_path(profile)
    df = load_parquet(dataset_path)
    validation = validate_market_dataset(profile, df)
    try:
        data_source = read_profile_manifest(profile.profile_id).get(
            "data_source",
            profile.data.download.provider or profile.data.provider,
        )
    except FileNotFoundError:
        data_source = profile.data.download.provider or profile.data.provider
    validation.update(
        {
            "profile_id": profile.profile_id,
            "data_source": data_source,
            "currency": profile.currency,
            "dataset_path": str(dataset_path),
            "row_count": df.height,
            "column_count": len(df.columns),
            "columns": list(df.columns),
            "status": "ok",
        }
    )
    return validation


def download_profile_data(
    profile_id: str | None = None,
    *,
    provider_override: str | None = None,
) -> DataDownloadResult:
    """根据交易画像下载或生成数据，并按规范写入数据目录与缓存目录。"""

    profile = load_trading_profile(profile_id)
    provider_id = provider_override or profile.data.download.provider or profile.data.provider
    provider = get_data_provider(provider_id)
    df = provider(profile)
    validation = validate_market_dataset(profile, df)

    cache_path = save_parquet(df, profile_download_cache_path(profile, provider_id))
    dataset_path = save_parquet(df, profile_market_data_path(profile))

    cache_manifest = save_json(
        _build_manifest(profile, provider_id, df, data_path=cache_path, validation=validation),
        dataset_manifest_path(cache_path),
    )
    dataset_manifest = save_json(
        _build_manifest(profile, provider_id, df, data_path=dataset_path, validation=validation),
        dataset_manifest_path(dataset_path),
    )

    start, end = _temporal_range(df)
    symbol_count = 0
    if "symbol" in df.columns and df.height > 0:
        symbol_count = len({str(symbol) for symbol in df.get_column("symbol").to_list()})

    return DataDownloadResult(
        profile_id=profile.profile_id,
        data_source=provider_id,
        currency=profile.currency,
        price_field=profile.data.price_field,
        schema_version=str(validation["schema_version"]),
        dataset_path=str(dataset_path),
        dataset_manifest_path=str(dataset_manifest),
        cache_path=str(cache_path),
        cache_manifest_path=str(cache_manifest),
        row_count=df.height,
        symbol_count=symbol_count,
        columns=list(df.columns),
        start=start,
        end=end,
    )


def list_profile_data_summaries() -> list[dict[str, Any]]:
    """列出所有交易画像的数据配置摘要。"""

    summaries: list[dict[str, Any]] = []
    for profile_id in list_trading_profiles():
        profile = load_trading_profile(profile_id)
        summaries.append(
            {
                "profile_id": profile.profile_id,
                "name": profile.name,
                "dimensions": asdict(profile.dimensions),
                "market": profile.market,
                "asset_type": profile.asset_type,
                "data_frequency": profile.data_frequency,
                "rebalance_frequency": profile.rebalance_frequency,
                "strategy_family": profile.strategy_family,
                "dimension_key": profile.dimension_key,
                "dataset_id": profile.data.dataset_id,
                "data_source": profile.data.download.provider or profile.data.provider,
                "currency": profile.currency,
                "price_field": profile.data.price_field,
                "timezone": profile.timezone,
                "dataset_path": str(profile_market_data_path(profile)),
                "cache_path": str(profile_download_cache_path(profile)),
                "symbols": list(profile.data.download.symbols),
            }
        )
    return summaries


register_data_provider("demo", _demo_provider)
register_data_provider("local", _local_provider)
register_data_provider("yfinance", download_yfinance_dataset)
