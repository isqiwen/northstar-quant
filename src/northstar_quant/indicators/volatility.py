"""波动率类技术指标。"""

from __future__ import annotations

import polars as pl

from northstar_quant.indicators.validation import (
    grouped,
    prepare_frame,
    temporary_column_name,
    validate_window,
)


def historical_volatility(
    data: pl.DataFrame,
    *,
    value_column: str,
    window: int,
    output_column: str,
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算滚动历史波动率列（简单收益率的样本标准差）。

    先计算 ``r_t = P_t / P_{t-1} - 1``，再计算
    ``HV_t = std(r_{t-window+1}, ..., r_t)``。实现采用 Polars 默认的样本标准差
    ``ddof=1``，不做年化；若需年化，应由调用策略显式乘以 ``sqrt(年化期数)``。
    因收益率本身从第二个 bar 才出现，完整结果通常至少需要 ``window + 1`` 个价格。
    """

    validate_window(window)
    prepared = prepare_frame(
        data,
        required_columns=(value_column,),
        group_by=group_by,
        order_by=order_by,
    )
    returns = pl.col(value_column).pct_change()
    expression = grouped(returns.rolling_std(window_size=window), group_by)
    return prepared.with_columns(expression.alias(output_column))


def average_true_range(
    data: pl.DataFrame,
    *,
    high_column: str,
    low_column: str,
    close_column: str,
    window: int = 14,
    output_column: str = "atr",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算 Wilder 平滑的平均真实波幅（ATR）列。

    前收盘价为 ``C_{t-1}`` 时，真实波幅为
    ``TR_t = max(High_t - Low_t, |High_t - C_{t-1}|, |Low_t - C_{t-1}|)``；
    第一个 bar 没有前收盘价，使用 ``High_t - Low_t``。ATR 使用 ``α = 1/window``
    的 Wilder EMA 平滑 ``TR``。前 ``window - 1`` 个 bar 返回 null，ATR 是价格
    单位的波动尺度，不是百分比。
    """

    validate_window(window)
    prepared = prepare_frame(
        data,
        required_columns=(high_column, low_column, close_column),
        group_by=group_by,
        order_by=order_by,
    )
    previous_close_column = temporary_column_name(prepared, "atr_previous_close")
    true_range_column = temporary_column_name(prepared, "atr_true_range")
    result = prepared.with_columns(
        grouped(pl.col(close_column).shift(1), group_by).alias(previous_close_column)
    )
    high_low = pl.col(high_column) - pl.col(low_column)
    true_range = pl.when(pl.col(previous_close_column).is_null()).then(high_low).otherwise(
        pl.max_horizontal(
            high_low,
            (pl.col(high_column) - pl.col(previous_close_column)).abs(),
            (pl.col(low_column) - pl.col(previous_close_column)).abs(),
        )
    )
    result = result.with_columns(true_range.alias(true_range_column))
    atr = grouped(
        pl.col(true_range_column).ewm_mean(
            alpha=1.0 / window,
            adjust=False,
            min_samples=window,
        ),
        group_by,
    )
    return result.with_columns(atr.alias(output_column)).drop(
        [previous_close_column, true_range_column]
    )


def bollinger_bands(
    data: pl.DataFrame,
    *,
    value_column: str,
    window: int = 20,
    num_std: float = 2.0,
    middle_column: str = "bollinger_middle",
    upper_column: str = "bollinger_upper",
    lower_column: str = "bollinger_lower",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算布林带中轨、上轨和下轨，标准差使用总体标准差。

    中轨 ``M_t = SMA(P, window)_t``；令 ``σ_t`` 是同一窗口价格的总体标准差
    （``ddof=0``），上、下轨分别为
    ``U_t = M_t + num_std * σ_t``、``L_t = M_t - num_std * σ_t``。
    默认窗口 20、标准差倍数 2。前 ``window - 1`` 个 bar 为 null；``num_std``
    必须为正数。
    """

    validate_window(window)
    if num_std <= 0:
        raise ValueError("num_std 必须大于 0")
    prepared = prepare_frame(
        data,
        required_columns=(value_column,),
        group_by=group_by,
        order_by=order_by,
    )
    middle = grouped(pl.col(value_column).rolling_mean(window_size=window), group_by)
    standard_deviation = grouped(
        pl.col(value_column).rolling_std(window_size=window, ddof=0),
        group_by,
    )
    return prepared.with_columns(
        [
            middle.alias(middle_column),
            (middle + num_std * standard_deviation).alias(upper_column),
            (middle - num_std * standard_deviation).alias(lower_column),
        ]
    )
