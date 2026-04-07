"""多策略组合层。"""

from __future__ import annotations

import polars as pl


def combine_strategy_targets(strategy_frames: list[pl.DataFrame], capital_weights: list[float]) -> pl.DataFrame:
    """合并多个策略目标仓位。

    参数说明：
    - strategy_frames：每个策略最新一期目标仓位
    - capital_weights：每个策略在总资金中的分配比例

    返回结果：
    - 聚合后的 symbol -> target_weight
    """

    if not strategy_frames:
        return pl.DataFrame({"symbol": [], "target_weight": []})

    scaled = []
    for frame, weight in zip(strategy_frames, capital_weights, strict=False):
        if frame.is_empty():
            continue
        scaled.append(
            frame.select(
                "symbol",
                (pl.col("target_weight") * float(weight)).alias("target_weight"),
            )
        )

    if not scaled:
        return pl.DataFrame({"symbol": [], "target_weight": []})

    return (
        pl.concat(scaled)
        .group_by("symbol")
        .agg(pl.col("target_weight").sum())
        .sort("target_weight", descending=True)
    )


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
