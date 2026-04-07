"""权重处理工具。"""

from __future__ import annotations

import polars as pl


def normalize_weights(targets: pl.DataFrame) -> pl.DataFrame:
    """把权重归一化到总和为 1（若总权重不为 0）。

    对 long-only 的 ETF 轮动策略，这个工具很常见：
    先选出候选标的，再把候选权重按总和标准化。
    """

    if targets.is_empty():
        return targets

    total = float(targets["target_weight"].sum())
    if abs(total) < 1e-12:
        return targets

    return targets.with_columns((pl.col("target_weight") / total).alias("target_weight"))
