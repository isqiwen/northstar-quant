"""动量类技术指标。"""

from __future__ import annotations

import polars as pl

from northstar_quant.research.features.technical.validation import (
    grouped,
    prepare_frame,
    temporary_column_name,
    validate_window,
)


def rate_of_change(
    data: pl.DataFrame,
    *,
    value_column: str,
    periods: int,
    output_column: str,
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算指定周期的价格变动率（ROC）列。

    对 ``N = periods``，公式为 ``ROC_t = P_t / P_{t-N} - 1``。前 ``N`` 个 bar
    因缺少基准价格为 null；当基准价格为零时，Polars 的浮点运算结果应由调用方
    结合数据质量规则处理。每个 ``group_by`` 分组独立回看，``order_by`` 决定
    ``t-N`` 的时间顺序。
    """

    validate_window(periods, parameter="periods")
    prepared = prepare_frame(
        data,
        required_columns=(value_column,),
        group_by=group_by,
        order_by=order_by,
    )
    expression = pl.col(value_column) / pl.col(value_column).shift(periods) - 1.0
    return prepared.with_columns(grouped(expression, group_by).alias(output_column))


def relative_strength_index(
    data: pl.DataFrame,
    *,
    value_column: str,
    window: int = 14,
    output_column: str = "rsi",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算 Wilder 平滑的相对强弱指数（RSI），取值范围为 0 到 100。

    先计算 ``Δ_t = P_t - P_{t-1}``，再取 ``Gain_t = max(Δ_t, 0)`` 和
    ``Loss_t = max(-Δ_t, 0)``。二者分别以 ``α = 1 / window`` 做 Wilder EMA：
    ``AvgGain_t``、``AvgLoss_t``；最终
    ``RS_t = AvgGain_t / AvgLoss_t``，
    ``RSI_t = 100 - 100 / (1 + RS_t)``。

    达到完整预热窗口前返回 null；若平均涨跌均为零返回 50，只有平均跌幅为零时
    返回 100，避免零除。首个价格变动按零处理，仅用于预热计数。
    """

    validate_window(window)
    prepared = prepare_frame(
        data,
        required_columns=(value_column,),
        group_by=group_by,
        order_by=order_by,
    )
    change_column = temporary_column_name(prepared, "rsi_change")
    gain_column = temporary_column_name(prepared, "rsi_gain")
    loss_column = temporary_column_name(prepared, "rsi_loss")
    result = prepared.with_columns(
        grouped(pl.col(value_column).diff(), group_by).alias(change_column)
    ).with_columns(
        [
            pl.when(pl.col(change_column) > 0)
            .then(pl.col(change_column))
            .otherwise(0.0)
            .alias(gain_column),
            pl.when(pl.col(change_column) < 0)
            .then(-pl.col(change_column))
            .otherwise(0.0)
            .alias(loss_column),
        ]
    )
    average_gain = grouped(
        pl.col(gain_column).ewm_mean(
            alpha=1.0 / window,
            adjust=False,
            min_samples=window,
        ),
        group_by,
    )
    average_loss = grouped(
        pl.col(loss_column).ewm_mean(
            alpha=1.0 / window,
            adjust=False,
            min_samples=window,
        ),
        group_by,
    )
    rsi = (
        pl.when(average_gain.is_null() | average_loss.is_null())
        .then(None)
        .when((average_gain == 0) & (average_loss == 0))
        .then(50.0)
        .when(average_loss == 0)
        .then(100.0)
        .otherwise(100.0 - 100.0 / (1.0 + average_gain / average_loss))
    )
    return result.with_columns(rsi.alias(output_column)).drop(
        [change_column, gain_column, loss_column]
    )


def stochastic_oscillator(
    data: pl.DataFrame,
    *,
    high_column: str,
    low_column: str,
    close_column: str,
    window: int = 14,
    smoothing: int = 3,
    k_column: str = "stochastic_k",
    d_column: str = "stochastic_d",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算随机指标 %K 与其 ``smoothing`` 周期均线 %D。

    在 ``N = window`` 内，``HH_t`` 为最高价最大值、``LL_t`` 为最低价最小值：
    ``%K_t = 100 * (Close_t - LL_t) / (HH_t - LL_t)``；
    ``%D_t = SMA(%K, smoothing)_t``。当 ``HH_t == LL_t`` 时 %K/%D 返回 null，
    不将无价格区间误写为超买或超卖信号。%K 需完整 ``window``，%D 还需完整
    ``smoothing`` 个有效 %K。
    """

    validate_window(window)
    validate_window(smoothing, parameter="smoothing")
    prepared = prepare_frame(
        data,
        required_columns=(high_column, low_column, close_column),
        group_by=group_by,
        order_by=order_by,
    )
    highest_column = temporary_column_name(prepared, "stochastic_high")
    lowest_column = temporary_column_name(prepared, "stochastic_low")
    result = prepared.with_columns(
        [
            grouped(pl.col(high_column).rolling_max(window_size=window), group_by).alias(
                highest_column
            ),
            grouped(pl.col(low_column).rolling_min(window_size=window), group_by).alias(
                lowest_column
            ),
        ]
    )
    price_range = pl.col(highest_column) - pl.col(lowest_column)
    result = result.with_columns(
        pl.when(price_range == 0)
        .then(None)
        .otherwise((pl.col(close_column) - pl.col(lowest_column)) / price_range * 100.0)
        .alias(k_column)
    )
    d_value = grouped(pl.col(k_column).rolling_mean(window_size=smoothing), group_by)
    return result.with_columns(d_value.alias(d_column)).drop([highest_column, lowest_column])


def williams_r(
    data: pl.DataFrame,
    *,
    high_column: str,
    low_column: str,
    close_column: str,
    window: int = 14,
    output_column: str = "williams_r",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算 Williams %R 列，取值范围通常为 -100 到 0。

    在 ``N = window`` 内，令 ``HH_t`` 为最高价最大值、``LL_t`` 为最低价最小值，
    公式为 ``%R_t = -100 * (HH_t - Close_t) / (HH_t - LL_t)``。价格接近窗口
    高点时接近 0，接近窗口低点时接近 -100。若 ``HH_t == LL_t`` 返回 null；
    前 ``N - 1`` 个 bar 为 null。
    """

    validate_window(window)
    prepared = prepare_frame(
        data,
        required_columns=(high_column, low_column, close_column),
        group_by=group_by,
        order_by=order_by,
    )
    highest = grouped(pl.col(high_column).rolling_max(window_size=window), group_by)
    lowest = grouped(pl.col(low_column).rolling_min(window_size=window), group_by)
    price_range = highest - lowest
    expression = pl.when(price_range == 0).then(None).otherwise(
        -100.0 * (highest - pl.col(close_column)) / price_range
    )
    return prepared.with_columns(expression.alias(output_column))
