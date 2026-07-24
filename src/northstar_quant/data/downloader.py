"""数据下载、标准化落盘与数据血缘 manifest 管理。

下载缓存和研究输入数据会各写一份相同的标准表及 manifest：缓存保留供应商下载口径，
market 目录是策略唯一读取入口。任何提供器返回的数据都必须先通过 schema 校验，不能
因下载成功就视为可用于研究或实盘。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from northstar_quant.config.trading_profile import TradingProfile, load_trading_profile, list_trading_profiles
from northstar_quant.data.providers.akshare import download_akshare_main_continuous
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

DataProvider = Callable[[TradingProfile], pl.DataFrame]

_PROVIDERS: dict[str, DataProvider] = {}


@dataclass(slots=True)
class DataDownloadResult:
    """一次下载的可序列化结果，所有路径均指向本地运行产物而非仓库受控文件。"""

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
    symbol_quality: list[dict[str, Any]]


def register_data_provider(provider_id: str, provider: DataProvider, *, replace: bool = False) -> None:
    """注册无状态数据提供器；重复 ID 默认拒绝，防止测试或插件静默替换正式来源。"""

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


def _temporal_range(df: pl.DataFrame) -> tuple[str | None, str | None]:
    """优先从 timestamp、其次从 date 提取数据实际覆盖区间，用于 manifest 审计。"""

    for column in ("timestamp", "date"):
        if column in df.columns and df.height > 0:
            series = df.get_column(column)
            return str(series.min()), str(series.max())
    return None, None


def _symbol_quality_summary(df: pl.DataFrame) -> list[dict[str, Any]]:
    """按标的生成日线下载质量摘要。

    ``calendar_gap_count_over_7_days`` 是相邻两根 bar 相隔超过七个自然日的次数，只能
    用于发现明显断档；它不是严格的“缺失交易日”计数，因为不同期货交易所的节假日和
    夜盘安排不同。严格交易日历校验应在未来接入交易所日历后单独实现。
    """

    time_column = next((column for column in ("timestamp", "date") if column in df.columns), None)
    if time_column is None or "symbol" not in df.columns or df.height == 0:
        return []

    summaries: list[dict[str, Any]] = []
    for symbol in sorted({str(value) for value in df.get_column("symbol").to_list()}):
        values = (
            df.filter(pl.col("symbol") == symbol)
            .select(time_column)
            .sort(time_column)
            .get_column(time_column)
            .to_list()
        )
        calendar_gaps = [
            (current - previous).days
            for previous, current in zip(values, values[1:], strict=False)
            if (current - previous).days > 7
        ]
        summaries.append(
            {
                "symbol": symbol,
                "start": str(values[0]),
                "end": str(values[-1]),
                "latest_bar_date": str(values[-1]),
                "row_count": len(values),
                "calendar_gap_count_over_7_days": len(calendar_gaps),
                "max_calendar_gap_days": max(calendar_gaps, default=0),
            }
        )
    return summaries


def _load_existing_manifest(path: Path) -> dict[str, Any] | None:
    """读取既有 manifest；缺失时视为首次下载，其他读取错误必须显式暴露。"""

    manifest_path = dataset_manifest_path(path)
    if not manifest_path.exists():
        return None
    return load_json(manifest_path)


def _quality_regression_issues(
    previous_manifest: dict[str, Any] | None,
    current_quality: list[dict[str, Any]],
) -> list[str]:
    """识别覆盖区间回退或历史行数显著缩水，防止坏下载覆盖已有研究数据。"""

    if previous_manifest is None:
        return []
    previous_quality = previous_manifest.get("quality", {}).get("symbols", [])
    previous_by_symbol = {
        str(item["symbol"]): item
        for item in previous_quality
        if isinstance(item, dict) and "symbol" in item
    }
    issues: list[str] = []
    for current in current_quality:
        symbol = str(current["symbol"])
        previous = previous_by_symbol.get(symbol)
        if previous is None:
            continue
        if str(current["start"]) > str(previous.get("start", "")):
            issues.append(f"{symbol} 起始日期从 {previous['start']} 缩短为 {current['start']}")
        if str(current["end"]) < str(previous.get("end", "")):
            issues.append(f"{symbol} 最新日期从 {previous['end']} 回退为 {current['end']}")
        previous_rows = int(previous.get("row_count", 0))
        current_rows = int(current["row_count"])
        if previous_rows > 0 and current_rows < previous_rows * 0.95:
            issues.append(f"{symbol} 行数从 {previous_rows} 降至 {current_rows}")
    return issues


def _build_manifest(
    profile: TradingProfile,
    data_source: str,
    df: pl.DataFrame,
    *,
    data_path: Path,
    validation: dict[str, Any],
    symbol_quality: list[dict[str, Any]],
) -> dict[str, Any]:
    """构建不可替代原始数据本身的轻量血缘记录。

    manifest 记录画像、来源、字段、标的、覆盖区间和下载选项，以便 preflight 与研究
    报告检查“当前文件从何而来”。它不证明供应商数据正确或可用于真实交易。
    """

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
        "live_trading_eligible": profile.data.live_trading_eligible,
        "data_path": str(data_path),
        "row_count": df.height,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "columns": list(df.columns),
        "start": start,
        "end": end,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema": validation,
        "quality": {
            "time_column": "timestamp" if "timestamp" in df.columns else "date",
            "symbols": symbol_quality,
        },
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
    """读取已落盘数据并校验标准 schema，返回可展示的校验摘要。

    此函数不下载、不修复也不补值；文件不存在或不合格都会抛错，确保调用者不会在
    错误数据上继续生成信号。
    """

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
    """根据画像自动下载数据，校验后同时写入缓存和标准研究目录。

    顺序固定为“提供器返回 → schema 校验 → 质量回退检查 → 写缓存/标准表 → 写两个
    manifest”。文件以原子替换发布；覆盖区间回退或行数显著缩水会在写入前被拒绝，防止
    供应商异常结果污染已有研究数据。``provider_override`` 只供明确的命令行或测试覆盖，
    不能改变画像所声明的数据资格。
    """

    profile = load_trading_profile(profile_id)
    provider_id = provider_override or profile.data.download.provider or profile.data.provider
    provider = get_data_provider(provider_id)
    df = provider(profile)
    validation = validate_market_dataset(profile, df)
    symbol_quality = _symbol_quality_summary(df)

    dataset_target = profile_market_data_path(profile)
    regression_issues = _quality_regression_issues(_load_existing_manifest(dataset_target), symbol_quality)
    if regression_issues:
        detail = "；".join(regression_issues)
        raise ValueError(f"下载数据质量回退，已拒绝覆盖现有数据：{detail}")

    cache_path = save_parquet(df, profile_download_cache_path(profile, provider_id))
    dataset_path = save_parquet(df, dataset_target)

    cache_manifest = save_json(
        _build_manifest(
            profile,
            provider_id,
            df,
            data_path=cache_path,
            validation=validation,
            symbol_quality=symbol_quality,
        ),
        dataset_manifest_path(cache_path),
    )
    dataset_manifest = save_json(
        _build_manifest(
            profile,
            provider_id,
            df,
            data_path=dataset_path,
            validation=validation,
            symbol_quality=symbol_quality,
        ),
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
        symbol_quality=symbol_quality,
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
                "role": profile.lifecycle.role,
                "line_id": profile.lifecycle.line_id,
                "is_production": profile.is_production,
                "dimensions": asdict(profile.dimensions),
                "market": profile.market,
                "asset_type": profile.asset_type,
                "data_frequency": profile.data_frequency,
                "rebalance_frequency": profile.rebalance_frequency,
                "strategy_family": profile.strategy_family,
                "dimension_key": profile.dimension_key,
                "dataset_id": profile.data.dataset_id,
                "data_source": profile.data.download.provider or profile.data.provider,
                "live_trading_eligible": profile.data.live_trading_eligible,
                "currency": profile.currency,
                "price_field": profile.data.price_field,
                "benchmark_symbol": profile.benchmark_symbol,
                "versions": asdict(profile.versions),
                "execution": asdict(profile.execution),
                "risk": dict(profile.risk),
                "timezone": profile.timezone,
                "dataset_path": str(profile_market_data_path(profile)),
                "cache_path": str(profile_download_cache_path(profile)),
                "symbols": list(profile.data.download.symbols),
            }
        )
    return summaries


register_data_provider("akshare", download_akshare_main_continuous)
