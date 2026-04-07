"""Yahoo Finance data provider."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import polars as pl

from northstar_quant.common.enums import DataFrequency
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.logging_.logger import get_logger

logger = get_logger(__name__, command="data.download")

_INTERVAL_MAP = {
    DataFrequency.M1: "1m",
    DataFrequency.M5: "5m",
    DataFrequency.M15: "15m",
    DataFrequency.H1: "60m",
    DataFrequency.D1: "1d",
    DataFrequency.W1: "1wk",
}
_INTRADAY_LOOKBACK_LIMITS = {
    DataFrequency.M1: 7,
    DataFrequency.M5: 60,
    DataFrequency.M15: 60,
    DataFrequency.H1: 730,
}


def _import_yfinance():
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "当前环境未安装 yfinance，无法使用 Yahoo Finance 下载。"
            "请先执行 `uv pip install yfinance` 或重新安装项目依赖。"
        ) from exc
    return yf


def _resolve_date_range(profile: TradingProfile) -> tuple[str, str]:
    start = profile.data.download.start_date
    if not start:
        raise ValueError(f"画像 {profile.profile_id} 未配置 download.start_date")

    end = profile.data.download.end_date or date.today().isoformat()
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    if end_day < start_day:
        raise ValueError(f"结束日期 {end} 早于开始日期 {start}")
    return start, end


def _validate_intraday_window(profile: TradingProfile, start: str, end: str) -> None:
    limit_days = _INTRADAY_LOOKBACK_LIMITS.get(profile.data_frequency)
    if limit_days is None:
        return
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    if (end_day - start_day).days > limit_days:
        raise ValueError(
            f"Yahoo Finance 的 {profile.data_frequency.value} 数据最长只支持约 {limit_days} 天窗口，"
            f"当前请求区间为 {start} 到 {end}。"
        )


def _normalize_symbol_frame(
    raw_df: pd.DataFrame,
    *,
    symbol: str,
    frequency: DataFrequency,
) -> pl.DataFrame:
    if raw_df.empty:
        return pl.DataFrame()

    frame = raw_df.reset_index()
    index_name = frame.columns[0]
    normalized_columns: list[str] = []
    for column in frame.columns:
        if isinstance(column, tuple):
            parts = [str(part).strip() for part in column if str(part).strip()]
            column_name = parts[0] if parts else ""
        else:
            column_name = str(column).strip()
        normalized_columns.append(column_name.lower().replace(" ", "_"))
    frame.columns = normalized_columns
    time_column = "date" if frequency in {DataFrequency.D1, DataFrequency.W1} else "timestamp"
    if isinstance(index_name, tuple):
        index_parts = [str(part).strip() for part in index_name if str(part).strip()]
        renamed_index = index_parts[0].lower().replace(" ", "_") if index_parts else "index"
    else:
        renamed_index = str(index_name).strip().lower().replace(" ", "_")
    frame = frame.rename(columns={renamed_index: time_column})

    required_price_columns = ["open", "high", "low", "close", "volume"]
    if frequency in {DataFrequency.D1, DataFrequency.W1}:
        required_price_columns.append("adj_close")
    missing = [column for column in required_price_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Yahoo Finance 返回缺少字段: {', '.join(missing)}")

    if time_column == "date":
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        if "adj_close" not in frame.columns:
            frame["adj_close"] = frame["close"]
        if "dividends" not in frame.columns:
            frame["dividends"] = 0.0
        if "stock_splits" not in frame.columns:
            frame["stock_splits"] = 0.0
        selected = frame[
            [
                "date",
                "open",
                "high",
                "low",
                "close",
                "adj_close",
                "volume",
                "dividends",
                "stock_splits",
            ]
        ].copy()
        selected["adjusted_close"] = selected["adj_close"]
        selected["dividend"] = selected["dividends"].fillna(0.0)
        selected["split_factor"] = (
            selected["stock_splits"].fillna(0.0).replace(0.0, 1.0)
        )
    else:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_localize(None)
        frame["date"] = frame["timestamp"].dt.date
        selected = frame[
            ["date", "timestamp", "open", "high", "low", "close", "volume"]
        ].copy()

    selected["symbol"] = symbol
    selected["volume"] = selected["volume"].fillna(0)

    if time_column == "date":
        return pl.from_pandas(
            selected[
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
        )
    return pl.from_pandas(
        selected[["date", "timestamp", "symbol", "open", "high", "low", "close", "volume"]]
    )


def download_yfinance_dataset(profile: TradingProfile) -> pl.DataFrame:
    """Download market data from Yahoo Finance according to a trading profile."""

    yf = _import_yfinance()
    symbols = list(profile.data.download.symbols)
    if not symbols:
        raise ValueError(f"画像 {profile.profile_id} 未配置 download.symbols")

    start, end_inclusive = _resolve_date_range(profile)
    _validate_intraday_window(profile, start, end_inclusive)

    interval = _INTERVAL_MAP.get(profile.data_frequency)
    if interval is None:
        raise ValueError(f"暂不支持的数据频率: {profile.data_frequency.value}")

    options: dict[str, Any] = dict(profile.data.download.options)
    auto_adjust = bool(options.get("auto_adjust", profile.data.adjusted))
    timeout = float(options.get("timeout_seconds", 30.0))
    allow_partial = bool(options.get("allow_partial", False))
    end_exclusive = (date.fromisoformat(end_inclusive) + timedelta(days=1)).isoformat()
    use_daily_actions = profile.data_frequency in {DataFrequency.D1, DataFrequency.W1}
    if use_daily_actions:
        auto_adjust = False

    frames: list[pl.DataFrame] = []
    missing_symbols: list[str] = []

    for symbol in symbols:
        logger.bind(
            provider="yfinance",
            profile=profile.profile_id,
            symbol=symbol,
            start=start,
            end=end_inclusive,
            interval=interval,
        ).info("开始下载 Yahoo Finance 数据")

        raw_df = yf.download(
            tickers=symbol,
            start=start,
            end=end_exclusive,
            interval=interval,
            auto_adjust=auto_adjust,
            progress=False,
            actions=use_daily_actions,
            threads=False,
            timeout=timeout,
        )
        normalized = _normalize_symbol_frame(raw_df, symbol=symbol, frequency=profile.data_frequency)
        if normalized.is_empty():
            missing_symbols.append(symbol)
            continue
        frames.append(normalized)

    if missing_symbols and not allow_partial:
        missing_text = ", ".join(missing_symbols)
        raise ValueError(f"以下符号未能从 Yahoo Finance 下载到数据: {missing_text}")

    if not frames:
        raise ValueError("Yahoo Finance 下载结果为空")

    combined = pl.concat(frames).sort(
        ["symbol", "date"] if profile.data_frequency in {DataFrequency.D1, DataFrequency.W1} else ["symbol", "timestamp"]
    )
    logger.bind(
        provider="yfinance",
        profile=profile.profile_id,
        row_count=combined.height,
        symbol_count=len({str(value) for value in combined.get_column("symbol").to_list()}),
        start=start,
        end=end_inclusive,
    ).info("Yahoo Finance 数据下载完成")
    return combined
