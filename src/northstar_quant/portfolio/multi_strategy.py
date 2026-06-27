"""多策略组合层。"""

from __future__ import annotations

import polars as pl

from northstar_quant.portfolio.allocator import normalize_weights
from northstar_quant.risk.global_risk import enforce_global_risk
from northstar_quant.risk.models import RiskLimits
from northstar_quant.risk.strategy_risk import enforce_strategy_risk


def _empty_target_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "symbol": pl.String,
            "signal_value": pl.Float64,
            "target_weight": pl.Float64,
        }
    )


def _empty_target_history_frame(time_column: str) -> pl.DataFrame:
    time_dtype = pl.Date if time_column == "date" else pl.Datetime
    return pl.DataFrame(
        schema={
            time_column: time_dtype,
            "symbol": pl.String,
            "signal_value": pl.Float64,
            "target_weight": pl.Float64,
        }
    )


def combine_strategy_targets(strategy_frames: list[pl.DataFrame], capital_weights: list[float]) -> pl.DataFrame:
    """合并多个策略目标仓位。

    参数说明：
    - strategy_frames：每个策略最新一期目标仓位
    - capital_weights：每个策略在总资金中的分配比例

    返回结果：
    - 聚合后的 symbol -> target_weight

    组合前会先把每个策略自己的目标权重标准化，再按 capital weight 缩放。
    这样组合层拿到的是“各策略内部自洽”的仓位向量，而不是把风险约束后的最终结果
    再次归一化回满仓。
    """

    if not strategy_frames:
        return _empty_target_frame()

    scaled: list[pl.DataFrame] = []
    for frame, weight in zip(strategy_frames, capital_weights, strict=False):
        if frame.is_empty():
            continue
        normalized = normalize_weights(frame)
        signal_expr = (
            (pl.col("signal_value") * float(weight)).alias("weighted_signal_value")
            if "signal_value" in normalized.columns
            else pl.lit(None, dtype=pl.Float64).alias("weighted_signal_value")
        )
        scaled.append(
            normalized.select(
                "symbol",
                signal_expr,
                pl.lit(float(weight)).alias("signal_weight"),
                (pl.col("target_weight") * float(weight)).alias("target_weight"),
            )
        )

    if not scaled:
        return _empty_target_frame()

    combined = (
        pl.concat(scaled)
        .group_by("symbol")
        .agg(
            pl.col("weighted_signal_value").sum().alias("weighted_signal_value"),
            pl.col("signal_weight").sum().alias("signal_weight"),
            pl.col("target_weight").sum().alias("target_weight"),
        )
        .with_columns(
            pl.when(pl.col("signal_weight") > 0)
            .then(pl.col("weighted_signal_value") / pl.col("signal_weight"))
            .otherwise(None)
            .alias("signal_value")
        )
        .select("symbol", "signal_value", "target_weight")
        .sort("target_weight", descending=True)
    )
    return combined


def combine_strategy_target_history(
    strategy_frames: list[pl.DataFrame],
    capital_weights: list[float],
    *,
    time_column: str = "date",
) -> pl.DataFrame:
    """按时间切片合并多个策略的历史 target-weight 输出。"""

    if not strategy_frames:
        return _empty_target_history_frame(time_column)

    time_values: list[object] = []
    for frame in strategy_frames:
        if frame.is_empty() or time_column not in frame.columns:
            continue
        time_values.extend(frame[time_column].to_list())

    if not time_values:
        return _empty_target_history_frame(time_column)

    rows: list[pl.DataFrame] = []
    for current_time in sorted(set(time_values)):
        current_frames: list[pl.DataFrame] = []
        current_weights: list[float] = []
        for frame, weight in zip(strategy_frames, capital_weights, strict=False):
            if frame.is_empty() or time_column not in frame.columns:
                continue
            current_frame = frame.filter(pl.col(time_column) == current_time)
            if current_frame.is_empty():
                continue
            current_frames.append(current_frame)
            current_weights.append(weight)

        combined = combine_strategy_targets(current_frames, current_weights)
        if combined.is_empty():
            continue
        rows.append(combined.with_columns(pl.lit(current_time).alias(time_column)))

    if not rows:
        return _empty_target_history_frame(time_column)
    return pl.concat(rows).sort([time_column, "symbol"])


def build_target_weight_portfolio(
    strategy_frames: list[pl.DataFrame],
    capital_weights: list[float],
    limits: RiskLimits,
) -> pl.DataFrame:
    """构建最终 target-weight 组合。

    这里显式区分两个阶段：
    1. 先把策略原始输出归一化后做资金配比
    2. 再做组合层风险约束

    风险约束后的结果允许保留现金，不会再被归一化回满仓。
    """

    combined = combine_strategy_targets(strategy_frames, capital_weights)
    combined = enforce_strategy_risk(combined, limits)
    return enforce_global_risk(combined, limits)


def build_target_weight_portfolio_history(
    strategy_frames: list[pl.DataFrame],
    capital_weights: list[float],
    limits: RiskLimits,
    *,
    time_column: str = "date",
) -> pl.DataFrame:
    """构建最终 target-weight 历史组合。"""

    combined = combine_strategy_target_history(
        strategy_frames,
        capital_weights,
        time_column=time_column,
    )
    if combined.is_empty():
        return combined

    rows: list[pl.DataFrame] = []
    for current_time in combined[time_column].unique().sort().to_list():
        current_slice = combined.filter(pl.col(time_column) == current_time).drop(time_column)
        current_slice = enforce_strategy_risk(current_slice, limits)
        current_slice = enforce_global_risk(current_slice, limits)
        if current_slice.is_empty():
            continue
        rows.append(current_slice.with_columns(pl.lit(current_time).alias(time_column)))

    if not rows:
        return _empty_target_history_frame(time_column)
    return pl.concat(rows).sort([time_column, "symbol"])


def combine_strategy_execution_intents(
    strategy_frames: list[pl.DataFrame],
    capital_weights: list[float],
    *,
    time_column: str = "timestamp",
) -> pl.DataFrame:
    """合并多个执行意图策略的输出。"""

    if not strategy_frames:
        return pl.DataFrame(
            {
                time_column: [],
                "strategy_id": [],
                "symbol": [],
                "signal_value": [],
                "side": [],
                "size_fraction": [],
                "order_semantic": [],
                "order_type": [],
                "limit_price": [],
                "reason": [],
            }
        )

    scaled: list[pl.DataFrame] = []
    for frame, weight in zip(strategy_frames, capital_weights, strict=False):
        if frame.is_empty():
            continue
        scaled.append(
            frame.with_columns((pl.col("size_fraction") * float(weight)).alias("size_fraction"))
        )

    if not scaled:
        return pl.DataFrame(
            {
                time_column: [],
                "strategy_id": [],
                "symbol": [],
                "signal_value": [],
                "side": [],
                "size_fraction": [],
                "order_semantic": [],
                "order_type": [],
                "limit_price": [],
                "reason": [],
            }
        )

    return (
        pl.concat(scaled)
        .group_by(
            [
                time_column,
                "strategy_id",
                "symbol",
                "side",
                "order_semantic",
                "order_type",
                "limit_price",
                "reason",
            ]
        )
        .agg(
            pl.col("signal_value").mean().alias("signal_value"),
            pl.col("size_fraction").sum().alias("size_fraction"),
        )
        .sort([time_column, "symbol"])
    )
