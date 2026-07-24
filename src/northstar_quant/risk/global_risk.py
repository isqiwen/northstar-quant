"""全局风控。"""

from __future__ import annotations

import polars as pl

from northstar_quant.risk.models import RiskLimits


def enforce_global_risk(targets: pl.DataFrame, limits: RiskLimits) -> pl.DataFrame:
    """执行全局组合层约束。

    设策略原始目标权重为 ``w_i``，组合总暴露为 ``G = Σ|w_i|``，可用暴露为
    ``A = max(max_gross_exposure - min_cash_buffer, 0)``。当 ``G > A`` 时，统一按
    ``w'_i = w_i × A / G`` 缩放，保留多空方向和相对权重；否则原样返回。

    它只处理组合级权重缩放，不检查单标的上限、可用资金、涨跌停、合约乘数或订单
    数量。那些检查分别属于策略风控和预交易风控，不能用这里的缩放替代。
    """

    if targets.is_empty():
        return targets

    gross = float(targets["target_weight"].abs().sum())
    allowed = max(limits.max_gross_exposure - limits.min_cash_buffer, 0.0)

    if gross <= allowed or gross == 0:
        return targets

    scale = allowed / gross
    return targets.with_columns((pl.col("target_weight") * scale).alias("target_weight"))
