"""全局风控。"""

from __future__ import annotations

import polars as pl

from northstar_quant.risk.models import RiskLimits


def enforce_global_risk(targets: pl.DataFrame, limits: RiskLimits) -> pl.DataFrame:
    """执行全局组合层约束。

    当前实现重点保证：
    - 总绝对权重不超过 gross exposure
    - 保留最小现金缓冲

    这个函数的设计思想是“组合层统一处理”，
    不要把这些逻辑散落在每个单独策略里。
    """

    if targets.is_empty():
        return targets

    gross = float(targets["target_weight"].abs().sum())
    allowed = max(limits.max_gross_exposure - limits.min_cash_buffer, 0.0)

    if gross <= allowed or gross == 0:
        return targets

    scale = allowed / gross
    return targets.with_columns((pl.col("target_weight") * scale).alias("target_weight"))
