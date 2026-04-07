"""Helpers for dataset exploration and visualization."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import polars as pl


def _time_column(market_df: pl.DataFrame) -> str:
    return "timestamp" if "timestamp" in market_df.columns else "date"


def _has_date_only_time(market_df: pl.DataFrame) -> bool:
    time_column = _time_column(market_df)
    return market_df.schema.get(time_column) == pl.Date


def _normalize_date_columns(pdf: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in pdf.columns and not pdf.empty:
            pdf[column] = pd.to_datetime(pdf[column]).dt.date
    return pdf


def build_symbol_summary_table(market_df: pl.DataFrame) -> pd.DataFrame:
    """Build a per-symbol coverage and latest-value summary."""

    time_column = _time_column(market_df)
    aggregations: list[pl.Expr] = [
        pl.len().alias("rows"),
        pl.col(time_column).min().alias("start"),
        pl.col(time_column).max().alias("end"),
        pl.col("close").last().alias("latest_close"),
        pl.col("volume").sum().alias("total_volume"),
    ]
    if "adjusted_close" in market_df.columns:
        aggregations.append(pl.col("adjusted_close").last().alias("latest_adjusted_close"))
    if "dividend" in market_df.columns:
        aggregations.append(pl.col("dividend").sum().alias("total_dividend"))
    if "split_factor" in market_df.columns:
        aggregations.append(pl.col("split_factor").product().alias("cumulative_split_factor"))

    summary = (
        market_df.sort(["symbol", time_column])
        .group_by("symbol")
        .agg(aggregations)
        .sort("symbol")
    )
    pdf = summary.to_pandas()
    if _has_date_only_time(market_df):
        pdf = _normalize_date_columns(pdf, ["start", "end"])
    return pdf


def build_normalized_price_frame(
    market_df: pl.DataFrame,
    *,
    price_column: str,
    symbols: list[str] | None = None,
    max_points_per_symbol: int = 500,
) -> pd.DataFrame:
    """Return a long frame of normalized price paths for plotting."""

    time_column = _time_column(market_df)
    selected = market_df
    if symbols:
        selected = selected.filter(pl.col("symbol").is_in(symbols))
    if selected.is_empty():
        return pd.DataFrame(columns=["time", "symbol", "normalized_price"])

    pdf = (
        selected.select([time_column, "symbol", price_column])
        .sort(["symbol", time_column])
        .to_pandas()
    )
    pdf = pdf.rename(columns={time_column: "time"})
    pdf = pdf.dropna(subset=[price_column])
    if _has_date_only_time(market_df):
        pdf = _normalize_date_columns(pdf, ["time"])

    parts: list[pd.DataFrame] = []
    for symbol, group in pdf.groupby("symbol", sort=True):
        if group.empty:
            continue
        first_price = float(group[price_column].iloc[0])
        if not math.isfinite(first_price) or abs(first_price) < 1e-12:
            continue
        normalized = group.copy()
        normalized["normalized_price"] = normalized[price_column].astype(float) / first_price
        if len(normalized) > max_points_per_symbol:
            step = max(1, len(normalized) // max_points_per_symbol)
            normalized = normalized.iloc[::step].copy()
            if normalized.iloc[-1]["time"] != group.iloc[-1]["time"]:
                normalized = pd.concat([normalized, group.tail(1)], ignore_index=True)
                normalized["normalized_price"] = normalized[price_column].astype(float) / first_price
        parts.append(normalized[["time", "symbol", "normalized_price"]])

    if not parts:
        return pd.DataFrame(columns=["time", "symbol", "normalized_price"])
    return pd.concat(parts, ignore_index=True)


def build_recent_candles(
    market_df: pl.DataFrame,
    *,
    symbol: str,
    limit: int = 120,
) -> pd.DataFrame:
    """Return the latest candles for a specific symbol."""

    time_column = _time_column(market_df)
    columns = [time_column, "symbol", "open", "high", "low", "close", "volume"]
    if "adjusted_close" in market_df.columns:
        columns.append("adjusted_close")
    if "dividend" in market_df.columns:
        columns.append("dividend")

    frame = (
        market_df.filter(pl.col("symbol") == symbol)
        .sort(time_column)
        .tail(limit)
        .select(columns)
    )
    pdf = frame.to_pandas()
    if time_column in pdf.columns:
        pdf = pdf.rename(columns={time_column: "time"})
    if _has_date_only_time(market_df):
        pdf = _normalize_date_columns(pdf, ["time"])
    return pdf


def build_data_snapshot_table(
    market_df: pl.DataFrame,
    *,
    limit: int = 50,
) -> pd.DataFrame:
    """Return the latest raw rows for table inspection."""

    time_column = _time_column(market_df)
    pdf = market_df.sort(time_column, descending=True).head(limit).to_pandas()
    if _has_date_only_time(market_df):
        pdf = _normalize_date_columns(pdf, ["date"])
    return pdf


def build_data_overview_metrics(manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize the top-level metrics needed by the dashboard."""

    return {
        "data_source": manifest.get("data_source"),
        "currency": manifest.get("currency"),
        "price_field": manifest.get("price_field"),
        "row_count": manifest.get("row_count"),
        "symbol_count": manifest.get("symbol_count"),
        "start": manifest.get("start"),
        "end": manifest.get("end"),
        "market": manifest.get("market"),
        "asset_type": manifest.get("asset_type"),
        "data_frequency": manifest.get("data_frequency"),
        "rebalance_frequency": manifest.get("rebalance_frequency"),
    }
