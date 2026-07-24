"""权重处理工具。"""

from __future__ import annotations

import polars as pl


def normalize_weights(targets: pl.DataFrame) -> pl.DataFrame:
    """按绝对权重归一化，保留多空方向且令 gross exposure 为 1。

    这既适用于 long-only 组合，也适用于期货多空组合：先将策略原始信号缩放到
    同一 gross exposure，再由组合层的 ``max_gross_exposure`` 决定最终风险预算。

    注意：这个函数只应该用于策略原始输出或资金配比前的标准化阶段，
    不应该作用在已经过风险约束的最终组合结果上，否则会把留出的现金重新填满。
    """

    if targets.is_empty():
        return targets

    gross = float(targets["target_weight"].abs().sum())
    if gross < 1e-12:
        return targets

    return targets.with_columns((pl.col("target_weight") / gross).alias("target_weight"))
