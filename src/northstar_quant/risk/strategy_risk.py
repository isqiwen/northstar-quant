"""策略级风控。"""

from __future__ import annotations

import polars as pl

from northstar_quant.risk.models import RiskLimits


def enforce_strategy_risk(targets: pl.DataFrame, limits: RiskLimits) -> pl.DataFrame:
    """对单个策略输出做约束。

    当前主要做：
    - 单标的最大权重截断

    后续你可以继续加：
    - 行业暴露
    - 波动率动态缩放
    - 回撤触发降权
    """

    if targets.is_empty():
        return targets

    return targets.with_columns(
        pl.when(pl.col("target_weight") > limits.max_single_weight)
        .then(limits.max_single_weight)
        .when(pl.col("target_weight") < -limits.max_single_weight)
        .then(-limits.max_single_weight)
        .otherwise(pl.col("target_weight"))
        .alias("target_weight")
    )
