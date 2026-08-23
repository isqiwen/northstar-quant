"""AKShare 国内期货连续合约日线提供器。

此模块只负责把 AKShare/Sina 的主力连续合约历史数据转为项目统一 schema。
连续合约是研究序列，不是 CTP 可下单合约；调用方必须通过 CTP 映射取得当期
实际合约后，才可能进入执行路径。
"""

from __future__ import annotations

from datetime import date
from time import sleep
from typing import Any

import polars as pl

from northstar_quant.foundation.config.trading_profile import TradingProfile

_REQUIRED_SOURCE_COLUMNS = {"日期", "开盘价", "最高价", "最低价", "收盘价", "成交量"}


def download_akshare_main_continuous(profile: TradingProfile) -> pl.DataFrame:
    """下载画像配置的国内期货主力连续合约日线。

    ``data.download.options.vendor_symbols`` 必须显式给出每个内部连续 symbol
    对应的 AKShare/Sina 代码，例如 ``RB_CONT: RB0``。不允许猜测或回退到旧数据源，
    以免在研究时误用不同的连续合约口径。
    """

    _require_continuous_futures_profile(profile)
    config = profile.data.download
    vendor_symbols = _vendor_symbols(config.options, config.symbols)
    start_date = _as_provider_date(config.start_date, field_name="start_date", required=True)
    end_date = _as_provider_date(config.end_date, field_name="end_date", required=False)
    request_interval_seconds = _request_interval_seconds(config.options)

    frames: list[pl.DataFrame] = []
    for index, internal_symbol in enumerate(config.symbols):
        if index:
            sleep(request_interval_seconds)
        vendor_symbol = vendor_symbols[internal_symbol]
        raw = _fetch_main_continuous_history(vendor_symbol, start_date, end_date)
        frames.append(_standardize_main_continuous_history(raw, internal_symbol, vendor_symbol))

    if not frames:
        raise ValueError(f"画像 {profile.profile_id} 未配置 data.download.symbols")
    return pl.concat(frames, how="vertical").sort("date", "symbol")


def _fetch_main_continuous_history(vendor_symbol: str, start_date: str, end_date: str) -> Any:
    """调用 AKShare；依赖缺失或上游失败时明确终止本次下载。"""

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "未安装 AKShare，无法自动下载国内期货数据。请执行 `uv sync --extra data`。"
        ) from exc

    try:
        return ak.futures_main_sina(
            symbol=vendor_symbol,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        raise RuntimeError(
            f"AKShare 下载主力连续合约失败：{vendor_symbol}。"
            "请稍后重试；本次不会回退为旧数据或合成数据。"
        ) from exc


def _standardize_main_continuous_history(
    raw: Any,
    internal_symbol: str,
    vendor_symbol: str,
) -> pl.DataFrame:
    """把单个 AKShare 返回值转换为标准国内期货日线表。"""

    try:
        frame = pl.from_pandas(raw, include_index=False)
    except Exception as exc:
        raise ValueError(f"AKShare 返回的 {vendor_symbol} 数据无法转换为表格") from exc

    missing_columns = sorted(_REQUIRED_SOURCE_COLUMNS - set(frame.columns))
    if missing_columns:
        raise ValueError(
            f"AKShare 返回的 {vendor_symbol} 缺少字段：{', '.join(missing_columns)}"
        )
    if frame.is_empty():
        raise ValueError(f"AKShare 未返回 {vendor_symbol} 在指定日期范围内的数据")

    standardized = frame.select(
        pl.col("日期").cast(pl.Utf8).str.to_date(strict=False).alias("date"),
        pl.lit(internal_symbol).alias("symbol"),
        pl.col("开盘价").cast(pl.Float64, strict=False).alias("open"),
        pl.col("最高价").cast(pl.Float64, strict=False).alias("high"),
        pl.col("最低价").cast(pl.Float64, strict=False).alias("low"),
        pl.col("收盘价").cast(pl.Float64, strict=False).alias("close"),
        # 上游没有提供项目定义的复权连续序列，故显式采用未调整收盘价。
        pl.col("收盘价").cast(pl.Float64, strict=False).alias("adjusted_close"),
        pl.col("成交量").cast(pl.Float64, strict=False).alias("volume"),
    )
    invalid = standardized.filter(
        pl.any_horizontal(
            [
                pl.col("date").is_null(),
                pl.col("open").is_null() | (pl.col("open") <= 0),
                pl.col("high").is_null() | (pl.col("high") <= 0),
                pl.col("low").is_null() | (pl.col("low") <= 0),
                pl.col("close").is_null() | (pl.col("close") <= 0),
                pl.col("volume").is_null() | (pl.col("volume") < 0),
                pl.col("high") < pl.col("low"),
            ]
        )
    )
    if invalid.height:
        raise ValueError(f"AKShare 返回的 {vendor_symbol} 含无效 OHLCV 数据")
    return standardized


def _require_continuous_futures_profile(profile: TradingProfile) -> None:
    if profile.asset_type.value != "FUTURES" or profile.futures is None:
        raise ValueError("AKShare 主力连续下载器只支持国内期货画像")
    if not profile.futures.symbols_are_continuous:
        raise ValueError("AKShare 主力连续下载器只支持 symbols_are_continuous=true 的研究画像")


def _vendor_symbols(options: dict[str, Any], symbols: tuple[str, ...]) -> dict[str, str]:
    raw = options.get("vendor_symbols")
    if not isinstance(raw, dict):
        raise ValueError("data.download.options.vendor_symbols 必须是内部 symbol 到供应商代码的映射")
    normalized = {str(key).strip().upper(): str(value).strip().upper() for key, value in raw.items()}
    expected = set(symbols)
    if set(normalized) != expected or not all(normalized.values()):
        raise ValueError("vendor_symbols 必须且只能覆盖 data.download.symbols 中的全部非空 symbol")
    return normalized


def _as_provider_date(value: str | None, *, field_name: str, required: bool) -> str:
    if value is None:
        if required:
            raise ValueError(f"data.download.{field_name} 为 AKShare 下载必填项")
        return date.today().strftime("%Y%m%d")
    try:
        return date.fromisoformat(value).strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"data.download.{field_name} 必须使用 YYYY-MM-DD") from exc


def _request_interval_seconds(options: dict[str, Any]) -> float:
    value = options.get("request_interval_seconds", 1.0)
    if isinstance(value, bool):
        raise ValueError("data.download.options.request_interval_seconds 必须是非负数")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("data.download.options.request_interval_seconds 必须是非负数") from exc
    if seconds < 0:
        raise ValueError("data.download.options.request_interval_seconds 必须是非负数")
    return seconds
