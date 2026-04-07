"""数据存取工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from northstar_quant.config.trading_profile import TradingProfile, load_trading_profile
from northstar_quant.config.settings import get_settings
from northstar_quant.data.schema import to_signal_market_data


def _resolve_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return get_settings().project_root / p


def save_parquet(df: pl.DataFrame, path: str | Path) -> Path:
    """保存为 parquet 文件。"""

    path_obj = _resolve_path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path_obj)
    return path_obj


def load_parquet(path: str | Path) -> pl.DataFrame:
    """读取 parquet 文件。"""

    path_obj = _resolve_path(path)
    return pl.read_parquet(path_obj)


def dataset_path(relative_path: str | Path) -> Path:
    """返回结构化市场数据路径。"""

    settings = get_settings()
    path_obj = Path(relative_path)
    path = path_obj if path_obj.is_absolute() else settings.storage_dir / "market" / path_obj
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def market_data_path(filename: str) -> Path:
    return dataset_path(filename)


def downloads_path(relative_path: str | Path) -> Path:
    """返回下载缓存路径。"""

    settings = get_settings()
    path_obj = Path(relative_path)
    path = path_obj if path_obj.is_absolute() else settings.downloads_dir / path_obj
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def profile_market_data_path(profile: TradingProfile | str | None = None) -> Path:
    """根据交易画像解析其对应的数据文件路径。"""

    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    return dataset_path(profile_obj.data.path)


def load_profile_market_data(profile: TradingProfile | str | None = None) -> pl.DataFrame:
    """读取某个交易画像对应的市场数据。"""

    return load_parquet(profile_market_data_path(profile))


def load_profile_signal_data(profile: TradingProfile | str | None = None) -> pl.DataFrame:
    """读取面向研究、回测和信号生成的价格语义数据。"""

    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    return to_signal_market_data(profile_obj, load_profile_market_data(profile_obj))


def profile_download_cache_path(
    profile: TradingProfile | str | None = None,
    provider: str | None = None,
) -> Path:
    """返回某个交易画像对应的下载缓存路径。"""

    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    resolved_provider = provider or profile_obj.data.download.provider or profile_obj.data.provider
    return downloads_path(
        Path(resolved_provider)
        / profile_obj.market.lower()
        / profile_obj.asset_type.lower()
        / profile_obj.data_frequency.lower()
        / f"{profile_obj.data.dataset_id}.parquet"
    )


def dataset_manifest_path(path: str | Path) -> Path:
    """返回数据文件对应的 manifest 路径。"""

    path_obj = _resolve_path(path)
    return path_obj.with_suffix(".manifest.json")


def save_json(payload: dict[str, Any], path: str | Path) -> Path:
    """保存 JSON 文件。"""

    path_obj = _resolve_path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    path_obj.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path_obj


def load_json(path: str | Path) -> dict[str, Any]:
    """读取 JSON 文件。"""

    path_obj = _resolve_path(path)
    return json.loads(path_obj.read_text(encoding="utf-8"))
